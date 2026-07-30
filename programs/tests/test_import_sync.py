from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from attendance.models import AttendanceEvent, AttendanceSession
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Program,
    ProgramFeature,
    School,
    Student,
)


class ImportSyncTests(TestCase):
    def setUp(self):
        self.password = "password123"  # nosec B105
        self.user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password=self.password,
        )
        self.client.login(username="admin", password=self.password)  # nosec B106

    def _upload_csv(self, url_name, csv_text, extra_data=None, filename="import.csv"):
        payload = {
            "file": SimpleUploadedFile(
                filename, csv_text.encode("utf-8"), content_type="text/csv"
            )
        }
        if extra_data:
            payload.update(extra_data)
        return self.client.post(reverse(url_name), payload, follow=True)

    def test_student_import_accepts_current_template_headers(self):
        csv_text = "\n".join(
            [
                "First Name,Legal First Name,Last Name,Date of Birth,School,Graduation Year,Active",
                "Ada,Augusta Ada,Lovelace,2005-12-10,Byron Academy,2027,Yes",
            ]
        )

        response = self._upload_csv("student_import", csv_text)

        self.assertEqual(response.status_code, 200)
        student = Student.objects.get(last_name="Lovelace")
        self.assertEqual(student.legal_first_name, "Augusta Ada")
        self.assertEqual(student.first_name, "Ada")
        self.assertEqual(student.graduation_year, 2027)

    def test_parent_import_marks_imported_adult_as_parent(self):
        csv_text = "\n".join(
            [
                "First Name,Last Name,Email,Phone Number",
                "Marie,Curie,marie.curie@example.com,412-555-1911",
            ]
        )

        response = self._upload_csv("parent_import", csv_text)

        self.assertEqual(response.status_code, 200)
        parent = Adult.objects.get(first_name="Marie", last_name="Curie")
        self.assertTrue(parent.is_parent)

    def test_mentor_import_marks_existing_adult_as_mentor(self):
        adult = Adult.objects.create(first_name="Grace", last_name="Hopper")
        self.assertFalse(adult.is_mentor)

        csv_text = "\n".join(
            [
                "First Name,Last Name,Personal Email,Andrew Email,Role",
                "Grace,Hopper,grace.hopper@example.com,ghopper@andrew.cmu.edu,mentor",
            ]
        )

        response = self._upload_csv(
            "mentor_import", csv_text, extra_data={"overwrite": "1"}
        )

        self.assertEqual(response.status_code, 200)
        adult.refresh_from_db()
        self.assertTrue(adult.is_mentor)

    def test_relationship_import_links_even_without_relationship_label(self):
        student = Student.objects.create(
            legal_first_name="Katherine",
            first_name="Katherine",
            last_name="Johnson",
            date_of_birth="2008-08-26",
        )

        csv_text = "\n".join(
            [
                "Legal First Name,Last Name,Date of Birth,Primary Parent First Name,Primary Parent Last Name,Primary Parent Email,Primary Parent Relationship",
                "Katherine,Johnson,2008-08-26,Mary,Johnson,mary.johnson@example.com,",
            ]
        )

        response = self._upload_csv("relationship_import", csv_text)

        self.assertEqual(response.status_code, 200)
        rel = AdultStudentRelationship.objects.get(student=student)
        self.assertEqual(rel.relationship_to_student, "parent")

    def test_attendance_import_sets_optional_visitor_team_number(self):
        attendance_feature, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        program = Program.objects.create(name="Offseason Event")
        program.features.add(attendance_feature)

        csv_text = "\n".join(
            [
                "first_name,last_name,rfid,time_in_utc,time_out_utc,visitor_team_number",
                "Casey,Visitor,,2025-09-03T18:05:00Z,2025-09-03T19:05:00Z,3504",
            ]
        )

        response = self._upload_csv(
            "attendance_import", csv_text, extra_data={"program_id": str(program.id)}
        )

        self.assertEqual(response.status_code, 200)
        session = AttendanceSession.objects.get(program=program)
        self.assertEqual(session.visitor_name, "Casey Visitor")
        self.assertEqual(session.visitor_team_number, 3504)

        event = AttendanceEvent.objects.filter(
            program=program, event_type=AttendanceEvent.IN
        ).get()
        self.assertEqual(event.visitor_team_number, 3504)


class ImportSampleCsvIntegrationTests(TestCase):
    def setUp(self):
        self.password = "password123"  # nosec B105
        self.user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password=self.password,
        )
        self.client.login(username="admin", password=self.password)  # nosec B106

    def _download_csv(self, sample_url_name):
        response = self.client.get(reverse(sample_url_name))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        return response.content.decode("utf-8")

    def _post_csv(self, import_url_name, csv_text, extra_data=None):
        payload = {
            "file": SimpleUploadedFile(
                "sample.csv", csv_text.encode("utf-8"), content_type="text/csv"
            )
        }
        if extra_data:
            payload.update(extra_data)
        return self.client.post(reverse(import_url_name), payload, follow=True)

    def test_all_sample_csv_downloads_return_non_empty_csv(self):
        sample_urls = [
            "students_sample_csv",
            "parents_sample_csv",
            "relationships_sample_csv",
            "mentors_sample_csv",
            "schools_sample_csv",
            "attendance_sample_csv",
        ]

        for url_name in sample_urls:
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/csv", response["Content-Type"])
            body = response.content.decode("utf-8").strip()
            self.assertTrue(body)
            # Guard that each sample still includes a header row.
            self.assertIn(
                ",", body.splitlines()[0], msg=f"Missing CSV header for {url_name}"
            )

    def test_students_sample_csv_round_trips_through_student_import(self):
        csv_text = self._download_csv("students_sample_csv")

        response = self._post_csv("student_import", csv_text)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Student.objects.count(), 2)
        self.assertTrue(Student.objects.filter(last_name="Lovelace").exists())
        self.assertTrue(Student.objects.filter(last_name="Johnson").exists())

    def test_parents_sample_csv_round_trips_through_parent_import(self):
        csv_text = self._download_csv("parents_sample_csv")

        response = self._post_csv("parent_import", csv_text)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Adult.objects.filter(is_parent=True).count(), 2)

    def test_relationships_sample_csv_round_trips_through_relationship_import(self):
        Student.objects.create(
            legal_first_name="Augusta Ada",
            first_name="Ada",
            last_name="Lovelace",
            andrew_id="alovelac",
            date_of_birth="2005-12-10",
        )
        Student.objects.create(
            legal_first_name="Katherine",
            first_name="Katherine",
            last_name="Johnson",
            date_of_birth="2008-08-26",
        )

        csv_text = self._download_csv("relationships_sample_csv")

        response = self._post_csv("relationship_import", csv_text)

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(AdultStudentRelationship.objects.count(), 3)
        self.assertEqual(
            AdultStudentRelationship.objects.filter(
                student__last_name="Lovelace"
            ).count(),
            2,
        )
        self.assertTrue(
            AdultStudentRelationship.objects.filter(
                student__last_name="Lovelace", relationship_to_student="parent"
            ).exists()
        )
        self.assertTrue(
            AdultStudentRelationship.objects.filter(
                student__last_name="Johnson"
            ).exists()
        )

    def test_mentors_sample_csv_round_trips_through_mentor_import(self):
        csv_text = self._download_csv("mentors_sample_csv")

        response = self._post_csv("mentor_import", csv_text)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Adult.objects.filter(is_mentor=True).count(), 2)

    def test_schools_sample_csv_round_trips_through_school_import(self):
        csv_text = self._download_csv("schools_sample_csv")

        response = self._post_csv("school_import", csv_text)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(School.objects.count(), 3)

    def test_attendance_sample_csv_round_trips_through_attendance_import(self):
        attendance_feature, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance"}
        )
        program = Program.objects.create(name="Build Season")
        program.features.add(attendance_feature)

        csv_text = self._download_csv("attendance_sample_csv")

        response = self._post_csv(
            "attendance_import",
            csv_text,
            extra_data={"program_id": str(program.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AttendanceSession.objects.filter(program=program).count(), 3)
        self.assertTrue(
            AttendanceSession.objects.filter(
                program=program, visitor_name="Jordan Lee", visitor_team_number=3504
            ).exists()
        )
