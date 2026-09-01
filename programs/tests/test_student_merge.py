import datetime
import io
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from applications.models import Application
from attendance.models import (
    AttendanceEvent,
    AttendanceSession,
    DigitalSignout,
    DigitalSignoutConfig,
    RFIDCard,
    StudentPresence,
)
from audit.events import AuditEvent
from audit.models import AuditLog
from badges.models import Badge, StudentBadge
from outreach.models import OutreachEvent, OutreachShift, OutreachSignup
from programs.models import (
    Adult,
    AdultStudentRelationship,
    BackgroundCheck,
    Crew,
    Enrollment,
    Fee,
    FeeAssignment,
    Payment,
    Program,
    ProgramDocument,
    RaceEthnicity,
    RolePermission,
    School,
    SlidingScale,
    Student,
    StudentDocument,
    SubTeam,
    Team,
)


@override_settings(FILE_ENCRYPTION_KEY="ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=")
class StudentMergeTest(TestCase):
    def setUp(self):
        self.group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user = User.objects.create_user(
            username="admin",
            password="password",  # nosec B106
        )
        self.user.groups.add(self.group)
        self.client = Client()
        self.client.force_login(self.user)

        self.school = School.objects.create(name="Test High School")
        self.program = Program.objects.create(
            name="Test Program",
            start_date=timezone.localdate(),
        )

        RolePermission.objects.update_or_create(
            role="Mentor",
            section="student_info",
            defaults={"can_read": True, "can_write": False},
        )
        RolePermission.objects.update_or_create(
            role="Parent",
            section="student_info",
            defaults={"can_read": True, "can_write": False},
        )

    def _student(self, first="Jane", last="Doe", **kwargs):
        defaults = {
            "legal_first_name": first,
            "last_name": last,
            "school": self.school,
            "graduation_year": 2027,
        }
        defaults.update(kwargs)
        return Student.objects.create(**defaults)

    def _parent(self, first="Parent", last="Doe", **kwargs):
        defaults = {
            "legal_first_name": first,
            "last_name": last,
            "is_parent": True,
        }
        defaults.update(kwargs)
        return Adult.objects.create(**defaults)

    # --- Permissions & Access Tests ---

    def test_anonymous_user_redirected(self):
        self.client.logout()
        response = self.client.get(reverse("student_merge"))
        self.assertEqual(response.status_code, 302)

    def test_non_lead_mentor_denied(self):
        user = User.objects.create_user(
            username="mentor_only", password="password"  # nosec B106
        )
        Adult.objects.create(user=user, is_mentor=True)
        self.client.force_login(user)
        response = self.client.get(reverse("student_merge"))
        self.assertEqual(response.status_code, 302)

    def test_lead_mentor_can_access_page(self):
        response = self.client.get(reverse("student_merge"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "students/merge.html")

    def test_merge_page_lists_students(self):
        s1 = self._student("Alice", "Smith")
        s2 = self._student("Bob", "Jones")
        response = self.client.get(reverse("student_merge"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="keep" value="%s"' % s1.pk)
        self.assertContains(response, 'name="source" value="%s"' % s2.pk)
        self.assertContains(response, "Alice Smith")
        self.assertContains(response, "Bob Jones")

    # --- Validation Tests ---

    def test_cannot_merge_student_into_itself(self):
        s = self._student("Jane", "Doe")
        response = self.client.post(
            reverse("student_merge"),
            {"keep": s.pk, "source": s.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "source",
            "Choose a different student to merge in.",
        )
        self.assertTrue(Student.objects.filter(pk=s.pk).exists())

    def test_merge_requires_keep_and_source(self):
        response = self.client.post(reverse("student_merge"), {})
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "keep", "This field is required."
        )
        self.assertFormError(
            response.context["form"], "source", "This field is required."
        )

    # --- Basic Merge & Audit Tests ---

    def test_basic_merge_deletes_source_and_keeps_survivor(self):
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")

        response = self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )
        self.assertRedirects(response, reverse("student_list"))
        self.assertTrue(Student.objects.filter(pk=keep.pk).exists())
        self.assertFalse(Student.objects.filter(pk=source.pk).exists())

    def test_merge_creates_audit_log(self):
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        audit = AuditLog.objects.filter(
            event=AuditEvent.RECORDS_MERGED,
            resource_type="Student",
            resource_id=keep.pk,
        ).first()
        self.assertIsNotNone(audit)
        self.assertIn("Janet", audit.notes)
        self.assertIn("Jane", audit.notes)

    # --- Relationships Tests ---

    def test_merge_transfers_parent_relationships(self):
        p1 = self._parent("Mom", "Doe")
        p2 = self._parent("Dad", "Doe")
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")

        AdultStudentRelationship.objects.create(
            adult=p1, student=keep, relationship_to_student="mother"
        )
        AdultStudentRelationship.objects.create(
            adult=p2, student=source, relationship_to_student="father"
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(
            AdultStudentRelationship.objects.filter(student=keep).count(), 2
        )
        self.assertTrue(
            AdultStudentRelationship.objects.filter(adult=p1, student=keep).exists()
        )
        self.assertTrue(
            AdultStudentRelationship.objects.filter(adult=p2, student=keep).exists()
        )

    def test_merge_deduplicates_overlapping_parent_relationships(self):
        p1 = self._parent("Mom", "Doe")
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")

        AdultStudentRelationship.objects.create(
            adult=p1, student=keep, relationship_to_student="parent"
        )
        AdultStudentRelationship.objects.create(
            adult=p1,
            student=source,
            relationship_to_student="mother",
            specific_relationship="Biological Mother",
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(
            AdultStudentRelationship.objects.filter(student=keep).count(), 1
        )
        rel = AdultStudentRelationship.objects.get(adult=p1, student=keep)
        self.assertEqual(rel.specific_relationship, "Biological Mother")

    def test_merge_updates_primary_and_secondary_contacts(self):
        p1 = self._parent("Mom", "Doe")
        p2 = self._parent("Dad", "Doe")
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")

        rel1 = AdultStudentRelationship.objects.create(
            adult=p1, student=source, relationship_to_student="mother"
        )
        rel2 = AdultStudentRelationship.objects.create(
            adult=p2, student=source, relationship_to_student="father"
        )
        source.primary_contact_relationship = rel1
        source.secondary_contact_relationship = rel2
        source.save()

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertIsNotNone(keep.primary_contact_relationship)
        self.assertEqual(keep.primary_contact_relationship.adult, p1)
        self.assertIsNotNone(keep.secondary_contact_relationship)
        self.assertEqual(keep.secondary_contact_relationship.adult, p2)

    # --- Field Copying Tests ---

    def test_merge_copies_missing_profile_fields(self):
        keep = self._student(
            "Jane",
            "Doe",
            date_of_birth=datetime.date(1900, 1, 1),
            school=None,
            graduation_year=None,
        )
        actual_dob = datetime.date(2009, 5, 15)
        source = self._student(
            "Janet",
            "Doe",
            preferred_first_name="Janey",
            pronouns="she/her",
            date_of_birth=actual_dob,
            school=self.school,
            graduation_year=2028,
            address="123 Main St",
            city="Pittsburgh",
            state="PA",
            zip_code="15213",
            phone_number="412-555-1234",
            phone_type="cell",
            personal_email="janet@example.com",
            tshirt_size="M",
            discord_handle="janey#1234",
            allergies="Peanuts",
            dietary_restrictions="Vegetarian",
            medical_notes="Asthma inhaler in bag",
            interest_reason="Interested in coding",
            hoped_gains="Leadership skills",
            prior_robotics_experience="FLL for 2 years",
            referral_source="Friend",
            andrew_id="jdoe",
            andrew_email="jdoe@andrew.cmu.edu",
            andrew_id_expiration=datetime.date(2027, 8, 31),
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.preferred_first_name, "Janey")
        self.assertEqual(keep.pronouns, "she/her")
        self.assertEqual(keep.date_of_birth, actual_dob)
        self.assertEqual(keep.school, self.school)
        self.assertEqual(keep.graduation_year, 2028)
        self.assertEqual(keep.address, "123 Main St")
        self.assertEqual(keep.city, "Pittsburgh")
        self.assertEqual(keep.state, "PA")
        self.assertEqual(keep.zip_code, "15213")
        self.assertEqual(keep.phone_number, "412-555-1234")
        self.assertEqual(keep.personal_email, "janet@example.com")
        self.assertEqual(keep.tshirt_size, "M")
        self.assertEqual(keep.discord_handle, "janey#1234")
        self.assertEqual(keep.allergies, "Peanuts")
        self.assertEqual(keep.dietary_restrictions, "Vegetarian")
        self.assertEqual(keep.medical_notes, "Asthma inhaler in bag")
        self.assertEqual(keep.interest_reason, "Interested in coding")
        self.assertEqual(keep.hoped_gains, "Leadership skills")
        self.assertEqual(keep.prior_robotics_experience, "FLL for 2 years")
        self.assertEqual(keep.referral_source, "Friend")
        self.assertEqual(keep.andrew_id, "jdoe")
        self.assertEqual(keep.andrew_email, "jdoe@andrew.cmu.edu")
        self.assertEqual(keep.andrew_id_expiration, datetime.date(2027, 8, 31))

    def test_merge_copies_boolean_flags(self):
        keep = self._student("Jane", "Doe")
        source = self._student(
            "Janet",
            "Doe",
            can_receive_texts=True,
            on_discord=True,
            seen_once=True,
            first_has_account=True,
            first_attached_to_parent_account=True,
            first_signed_cr=True,
            graduated=True,
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertTrue(keep.can_receive_texts)
        self.assertTrue(keep.on_discord)
        self.assertTrue(keep.seen_once)
        self.assertTrue(keep.first_has_account)
        self.assertTrue(keep.first_attached_to_parent_account)
        self.assertTrue(keep.first_signed_cr)
        self.assertTrue(keep.graduated)

    def test_merge_copies_missing_photo(self):
        keep = self._student("Jane", "Doe")
        img_io = io.BytesIO()
        img = Image.new("RGB", (10, 10), color="blue")
        img.save(img_io, format="JPEG")
        img_io.seek(0)
        photo = SimpleUploadedFile(
            "student_avatar.jpg", img_io.getvalue(), content_type="image/jpeg"
        )
        source = self._student("Janet", "Doe", photo=photo)

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertTrue(bool(keep.photo))

    def test_merge_copies_race_ethnicities(self):
        r1, _ = RaceEthnicity.objects.get_or_create(name="Asian")
        r2, _ = RaceEthnicity.objects.get_or_create(name="White")
        keep = self._student("Jane", "Doe")
        keep.race_ethnicities.add(r1)
        source = self._student("Janet", "Doe")
        source.race_ethnicities.add(r2)

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.race_ethnicities.count(), 2)
        self.assertTrue(keep.race_ethnicities.filter(pk=r1.pk).exists())
        self.assertTrue(keep.race_ethnicities.filter(pk=r2.pk).exists())

    def test_merge_preserves_existing_keep_data(self):
        keep = self._student(
            "Jane",
            "Doe",
            personal_email="keep@example.com",
            address="Keep Address",
            graduation_year=2026,
        )
        source = self._student(
            "Janet",
            "Doe",
            personal_email="source@example.com",
            address="Source Address",
            graduation_year=2028,
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.personal_email, "keep@example.com")
        self.assertEqual(keep.address, "Keep Address")
        self.assertEqual(keep.graduation_year, 2026)

    # --- Related Records Tests ---

    def test_merge_transfers_and_merges_enrollments(self):
        p2 = Program.objects.create(
            name="Second Program", start_date=timezone.localdate()
        )
        team, _ = Team.objects.get_or_create(
            team_type="FRC", number=3504, defaults={"name": "Girls of Steel"}
        )
        crew = Crew.objects.create(name="Design Crew", program=self.program)
        subteam = SubTeam.objects.create(name="Chassis", program=self.program)

        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")

        # Overlapping enrollment in self.program
        Enrollment.objects.create(student=keep, program=self.program, active=False)
        Enrollment.objects.create(
            student=source,
            program=self.program,
            active=True,
            team=team,
            crew=crew,
            subteam=subteam,
            clearance_due=True,
        )

        # Non-overlapping enrollment in p2
        Enrollment.objects.create(student=source, program=p2, active=True)

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(Enrollment.objects.filter(student=keep).count(), 2)

        enr1 = Enrollment.objects.get(student=keep, program=self.program)
        self.assertTrue(enr1.active)
        self.assertEqual(enr1.team, team)
        self.assertEqual(enr1.crew, crew)
        self.assertEqual(enr1.subteam, subteam)
        self.assertTrue(enr1.clearance_due)

        self.assertTrue(Enrollment.objects.filter(student=keep, program=p2).exists())

    def test_merge_transfers_payments(self):
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")
        Enrollment.objects.create(student=keep, program=self.program)
        Enrollment.objects.create(student=source, program=self.program)

        p = Payment.objects.create(
            student=source,
            program=self.program,
            amount=Decimal("150.00"),
            paid_on=timezone.localdate(),
            paid_via="check",
            check_number=1234,
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        p.refresh_from_db()
        self.assertEqual(p.student, keep)

    def test_merge_transfers_and_merges_fee_assignments(self):
        fee1 = Fee.objects.create(
            program=self.program, name="Base Fee", amount=Decimal("100.00")
        )
        fee2 = Fee.objects.create(
            program=self.program, name="Trip Fee", amount=Decimal("50.00")
        )

        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")
        Enrollment.objects.create(student=keep, program=self.program)
        Enrollment.objects.create(student=source, program=self.program)

        # Overlapping assignment on fee1
        FeeAssignment.objects.create(fee=fee1, student=keep, notes="")
        FeeAssignment.objects.create(fee=fee1, student=source, notes="Transferred Note")

        # Non-overlapping assignment on fee2
        FeeAssignment.objects.create(fee=fee2, student=source, notes="Trip Assignment")

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertEqual(FeeAssignment.objects.filter(student=keep).count(), 2)
        fa1 = FeeAssignment.objects.get(fee=fee1, student=keep)
        self.assertEqual(fa1.notes, "Transferred Note")
        self.assertTrue(FeeAssignment.objects.filter(fee=fee2, student=keep).exists())

    def test_merge_transfers_sliding_scale(self):
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")
        parent = self._parent("Parent", "Doe")

        scale = SlidingScale.objects.create(
            student=source,
            applied_by=parent,
            percent=Decimal("50.00"),
            status="approved",
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        scale.refresh_from_db()
        self.assertEqual(scale.student, keep)

    def test_merge_transfers_student_documents(self):
        pdoc = ProgramDocument.objects.create(
            program=self.program,
            name="Release Form",
            file=SimpleUploadedFile(
                "release.pdf", b"%PDF-1.4", content_type="application/pdf"
            ),
        )
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")

        doc = StudentDocument.objects.create(
            student=source,
            program_document=pdoc,
            file=SimpleUploadedFile(
                "signed_release.pdf", b"%PDF-1.4", content_type="application/pdf"
            ),
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        doc.refresh_from_db()
        self.assertEqual(doc.student, keep)

    def test_merge_transfers_and_merges_background_checks(self):
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")
        today = timezone.localdate()

        BackgroundCheck.objects.create(
            student=keep,
            check_type="state_police",
            cleared=False,
            obtained_date=None,
        )
        BackgroundCheck.objects.create(
            student=source,
            check_type="state_police",
            cleared=True,
            obtained_date=today,
        )
        BackgroundCheck.objects.create(
            student=source,
            check_type="fbi",
            cleared=True,
            obtained_date=today,
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertEqual(BackgroundCheck.objects.filter(student=keep).count(), 2)
        sp = BackgroundCheck.objects.get(student=keep, check_type="state_police")
        self.assertTrue(sp.cleared)
        self.assertEqual(sp.obtained_date, today)
        self.assertTrue(
            BackgroundCheck.objects.filter(student=keep, check_type="fbi").exists()
        )

    def test_merge_transfers_rfid_cards(self):
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")
        RFIDCard.objects.create(student=source, uid="RFID-STUDENT-001")

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertTrue(
            RFIDCard.objects.filter(student=keep, uid="RFID-STUDENT-001").exists()
        )

    def test_merge_transfers_attendance_sessions_and_events(self):
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")

        session = AttendanceSession.objects.create(
            program=self.program, student=source, check_in=timezone.now()
        )
        event = AttendanceEvent.objects.create(
            program=self.program, student=source, event_type=AttendanceEvent.IN
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        session.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(session.student, keep)
        self.assertEqual(event.student, keep)

    def test_merge_transfers_and_merges_student_presence(self):
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")
        today = timezone.localdate()
        yesterday = today - datetime.timedelta(days=1)

        # Overlapping presence: keep is absent, source is present -> promote to present
        StudentPresence.objects.create(
            program=self.program,
            student=keep,
            date=today,
            status=StudentPresence.ABSENT,
        )
        StudentPresence.objects.create(
            program=self.program,
            student=source,
            date=today,
            status=StudentPresence.PRESENT,
        )

        # Non-overlapping presence on yesterday
        StudentPresence.objects.create(
            program=self.program,
            student=source,
            date=yesterday,
            status=StudentPresence.PRESENT,
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertEqual(StudentPresence.objects.filter(student=keep).count(), 2)
        p_today = StudentPresence.objects.get(student=keep, date=today)
        self.assertEqual(p_today.status, StudentPresence.PRESENT)
        self.assertTrue(
            StudentPresence.objects.filter(student=keep, date=yesterday).exists()
        )

    def test_merge_transfers_digital_signouts(self):
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")
        config = DigitalSignoutConfig.objects.create(
            label="Main Door", program=self.program
        )

        signout = DigitalSignout.objects.create(
            config=config,
            program=self.program,
            student=source,
            signed_by_name="Mom Doe",
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        signout.refresh_from_db()
        self.assertEqual(signout.student, keep)

    def test_merge_transfers_applications(self):
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")

        app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            program=self.program,
            email="janet@example.com",
            converted_student=source,
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        app.refresh_from_db()
        self.assertEqual(app.converted_student, keep)

    def test_merge_transfers_and_merges_outreach_signups(self):
        event = OutreachEvent.objects.create(
            program=self.program, name="Science Fair", location_name="Hall"
        )
        shift1 = OutreachShift.objects.create(
            event=event,
            date=timezone.localdate(),
            start_time=datetime.time(9, 0),
            end_time=datetime.time(12, 0),
        )
        shift2 = OutreachShift.objects.create(
            event=event,
            date=timezone.localdate(),
            start_time=datetime.time(13, 0),
            end_time=datetime.time(16, 0),
        )

        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")
        checkin_time = timezone.now()

        # Overlapping shift: keep is helper, source is champion with checkin -> promote
        OutreachSignup.objects.create(
            shift=shift1, student=keep, role=OutreachSignup.HELPER
        )
        OutreachSignup.objects.create(
            shift=shift1,
            student=source,
            role=OutreachSignup.CHAMPION,
            checked_in_at=checkin_time,
        )

        # Non-overlapping shift
        OutreachSignup.objects.create(
            shift=shift2, student=source, role=OutreachSignup.HELPER
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertEqual(OutreachSignup.objects.filter(student=keep).count(), 2)
        s1 = OutreachSignup.objects.get(shift=shift1, student=keep)
        self.assertEqual(s1.role, OutreachSignup.CHAMPION)
        self.assertEqual(s1.checked_in_at, checkin_time)
        self.assertTrue(
            OutreachSignup.objects.filter(shift=shift2, student=keep).exists()
        )

    def test_merge_transfers_badges(self):
        b1 = Badge.objects.create(name="Safety", level=1)
        b2 = Badge.objects.create(name="CAD", level=1)
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")

        StudentBadge.objects.create(student=keep, badge=b1)
        StudentBadge.objects.create(student=source, badge=b1)  # Duplicate
        StudentBadge.objects.create(student=source, badge=b2)

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertEqual(StudentBadge.objects.filter(student=keep).count(), 2)
        self.assertTrue(StudentBadge.objects.filter(student=keep, badge=b1).exists())
        self.assertTrue(StudentBadge.objects.filter(student=keep, badge=b2).exists())

    def test_merge_transfers_alumni_profile_link(self):
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe")
        alumni_adult = self._parent(
            "Jane", "Doe", is_alumni=True, student_record=source
        )

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        alumni_adult.refresh_from_db()
        self.assertEqual(alumni_adult.student_record, keep)

    def test_merge_transfers_user_account(self):
        source_user = User.objects.create_user(
            username="janet_student", password="password"  # nosec B106
        )
        keep = self._student("Jane", "Doe")
        source = self._student("Janet", "Doe", user=source_user)

        self.client.post(
            reverse("student_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.user, source_user)

    # --- UI Button on All Students page ---

    def test_merge_button_visible_to_lead_mentor_on_students_list(self):
        response = self.client.get(reverse("student_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("student_merge"))
        self.assertContains(response, "Merge Students")

    def test_merge_button_hidden_from_non_lead_mentor_on_students_list(self):
        user = User.objects.create_user(
            username="regular_mentor", password="password"  # nosec B106
        )
        Adult.objects.create(user=user, is_mentor=True)
        self.client.force_login(user)
        response = self.client.get(reverse("student_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("student_merge"))
        self.assertNotContains(response, "Merge Students")
