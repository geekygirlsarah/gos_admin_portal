import datetime
import io

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from attendance.models import AttendanceEvent, AttendanceSession, RFIDCard
from audit.events import AuditEvent
from audit.models import AuditLog
from outreach.models import OutreachEvent, OutreachMentorSignup, OutreachShift
from programs.models import (
    Adult,
    AdultStudentRelationship,
    BackgroundCheck,
    MentorAgreement,
    MentorAgreementAcceptance,
    MentorAgreementSubmission,
    Program,
    SlidingScale,
    Student,
)


class ParentMergeTest(TestCase):
    """Tests for the parent merge / consolidation feature."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="admin",
            password="password",  # nosec B106
        )
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)
        self.client = Client()
        self.client.force_login(self.user)

    def _parent(self, legal_first_name="Jane", last_name="Doe", **kwargs):
        defaults = {
            "legal_first_name": legal_first_name,
            "last_name": last_name,
            "is_parent": True,
            "login_enabled": True,
        }
        defaults.update(kwargs)
        return Adult.objects.create(**defaults)

    def _student(self, preferred_first_name="Test", last_name="Student", **kwargs):
        defaults = {
            "preferred_first_name": preferred_first_name,
            "last_name": last_name,
            "graduation_year": 2026,
        }
        defaults.update(kwargs)
        return Student.objects.create(**defaults)

    def _rel(self, adult, student, relationship="parent", specific=""):
        return AdultStudentRelationship.objects.create(
            adult=adult,
            student=student,
            relationship_to_student=relationship,
            specific_relationship=specific,
        )

    # --- GET page tests ---

    def test_merge_page_lists_parents_with_keep_and_source_options(self):
        p1 = self._parent("Jane", "Doe")
        p2 = self._parent("Janet", "Doe")

        response = self.client.get(reverse("parent_merge"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="keep" value="%s"' % p1.pk)
        self.assertContains(response, 'name="source" value="%s"' % p2.pk)
        self.assertContains(response, "Jane")
        self.assertContains(response, "Janet")

    def test_merge_page_orders_parents_by_first_then_last_name(self):
        zoe = self._parent("Zoe", "Adams")
        alice = self._parent("Alice", "Zephyr")
        mia = self._parent("Mia", "Brown")

        response = self.client.get(reverse("parent_merge"))

        parents = list(response.context["parents"])
        self.assertEqual([p.pk for p in parents], [alice.pk, mia.pk, zoe.pk])

    def test_merge_page_shows_parent_id_next_to_name(self):
        p1 = self._parent("Jane", "Doe")

        response = self.client.get(reverse("parent_merge"))

        self.assertContains(response, f"Jane Doe ({p1.pk})")

    # --- Core merge tests ---

    def test_merge_reassigns_relationships_and_deletes_source(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")
        student = self._student()
        self._rel(source, student)

        response = self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Adult.objects.filter(pk=source.pk).exists())
        self.assertTrue(
            AdultStudentRelationship.objects.filter(
                adult=keep, student=student
            ).exists()
        )
        self.assertFalse(
            AdultStudentRelationship.objects.filter(
                adult=source, student=student
            ).exists()
        )

    def test_merge_preserves_keep_fields(self):
        keep = self._parent("Jane", "Doe", personal_email="jane@example.com")
        source = self._parent("Janet", "Doe", personal_email="janet@example.com")

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.personal_email, "jane@example.com")

    def test_merge_fills_missing_keep_fields_from_source(self):
        keep = self._parent("Jane", "Doe", phone_number="555-1234")
        source = self._parent(
            "Janet", "Doe", phone_number="555-5678", city="Pittsburgh"
        )

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.phone_number, "555-1234")
        self.assertEqual(keep.city, "Pittsburgh")

    def test_merge_fills_all_missing_contact_fields_from_source(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent(
            "Janet",
            "Doe",
            personal_email="janet@example.com",
            phone_number="555-5678",
            phone_type="home",
            address="123 Main St",
            city="Pittsburgh",
            state="CA",
            zip_code="15201",
            pronouns="she/her",
            can_receive_texts=True,
        )

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.personal_email, "janet@example.com")
        self.assertEqual(keep.phone_number, "555-5678")
        self.assertEqual(keep.phone_type, "home")
        self.assertEqual(keep.address, "123 Main St")
        self.assertEqual(keep.city, "Pittsburgh")
        self.assertEqual(keep.state, "CA")
        self.assertEqual(keep.zip_code, "15201")
        self.assertEqual(keep.pronouns, "she/her")
        self.assertTrue(keep.can_receive_texts)

    def test_merge_copies_missing_emergency_contact_and_preferred_name(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent(
            "Janet",
            "Doe",
            preferred_first_name="Jan",
            emergency_contact_name="Bob Doe",
            emergency_contact_phone="555-4321",
        )

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.preferred_first_name, "Jan")
        self.assertEqual(keep.emergency_contact_name, "Bob Doe")
        self.assertEqual(keep.emergency_contact_phone, "555-4321")

    def test_merge_does_not_overwrite_existing_emergency_contact(self):
        keep = self._parent("Jane", "Doe", emergency_contact_name="Existing Name")
        source = self._parent("Janet", "Doe", emergency_contact_name="Source Name")

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.emergency_contact_name, "Existing Name")

    def test_cannot_merge_parent_into_itself(self):
        keep = self._parent("Jane", "Doe")

        response = self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": keep.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Adult.objects.filter(pk=keep.pk).exists())

    def test_merge_handles_shared_student(self):
        """Both parents relate to the same student — keep's relationship is
        preserved and source's is removed."""
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")
        student = self._student()
        self._rel(keep, student, specific="mother")
        self._rel(source, student, specific="stepmother")

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertFalse(Adult.objects.filter(pk=source.pk).exists())
        rel = AdultStudentRelationship.objects.get(adult=keep, student=student)
        self.assertEqual(rel.specific_relationship, "mother")

    def test_merge_updates_primary_contact_relationship(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")
        student = self._student()
        source_rel = self._rel(source, student)
        student.primary_contact_relationship = source_rel
        student.save(update_fields=["primary_contact_relationship"])

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        student.refresh_from_db()
        self.assertEqual(student.primary_contact, keep)

    def test_merge_updates_secondary_contact_relationship(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")
        student = self._student()
        source_rel = self._rel(source, student)
        student.secondary_contact_relationship = source_rel
        student.save(update_fields=["secondary_contact_relationship"])

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        student.refresh_from_db()
        self.assertEqual(student.secondary_contact, keep)

    def test_merge_transfers_user_account(self):
        keep = self._parent("Jane", "Doe")
        source_user = User.objects.create_user(
            username="source_user",
            password="password",  # nosec B106
        )
        source = self._parent("Janet", "Doe", user=source_user)

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.user, source_user)

    def test_merge_preserves_keep_user_account(self):
        keep_user = User.objects.create_user(
            username="keep_user",
            password="password",  # nosec B106
        )
        keep = self._parent("Jane", "Doe", user=keep_user)
        source_user = User.objects.create_user(
            username="source_user",
            password="password",  # nosec B106
        )
        source = self._parent("Janet", "Doe", user=source_user)

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.user, keep_user)

    def test_merge_merges_role_flags(self):
        keep = self._parent("Jane", "Doe", is_parent=True, is_mentor=False)
        source = self._parent("Janet", "Doe", is_parent=True, is_mentor=True)

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertTrue(keep.is_parent)
        self.assertTrue(keep.is_mentor)

    # --- Permission test ---

    def test_merge_requires_lead_mentor_permission(self):
        regular_user = User.objects.create_user(
            username="regular",
            password="password",  # nosec B106
        )
        self.client.force_login(regular_user)

        response = self.client.get(reverse("parent_merge"))
        self.assertEqual(response.status_code, 302)

    # --- Audit logging test ---

    def test_merge_creates_audit_log(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")
        student = self._student()
        self._rel(source, student)

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        audit = AuditLog.objects.filter(
            event=AuditEvent.RECORDS_MERGED,
            resource_type="Adult",
            resource_id=str(keep.pk),
        ).first()
        self.assertIsNotNone(audit)
        self.assertIn("Janet", audit.notes)
        self.assertIn("Jane", audit.notes)

    # --- Comprehensive Data Copying & Related Records Tests ---

    def test_merge_copies_missing_extended_profile_fields(self):
        keep = self._parent("Jane", "Doe")
        exp_date = datetime.date(2027, 6, 30)
        source = self._parent(
            "Janet",
            "Doe",
            start_year=2024,
            andrew_id="jdoe",
            andrew_email="jdoe@andrew.cmu.edu",
            andrew_id_expiration=exp_date,
            discord_username="jdoe#1234",
            college="Carnegie Mellon",
            field_of_study="Robotics",
            employer="CMU",
            job_title="Engineer",
            notes="Important notes about this person",
        )

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.start_year, 2024)
        self.assertEqual(keep.andrew_id, "jdoe")
        self.assertEqual(keep.andrew_email, "jdoe@andrew.cmu.edu")
        self.assertEqual(keep.andrew_id_expiration, exp_date)
        self.assertEqual(keep.discord_username, "jdoe#1234")
        self.assertEqual(keep.college, "Carnegie Mellon")
        self.assertEqual(keep.field_of_study, "Robotics")
        self.assertEqual(keep.employer, "CMU")
        self.assertEqual(keep.job_title, "Engineer")
        self.assertEqual(keep.notes, "Important notes about this person")

    def test_merge_copies_boolean_capability_flags(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent(
            "Janet",
            "Doe",
            on_discord=True,
            has_cmu_id_card=True,
            has_cmu_building_access=True,
            has_google_team_drive_access=True,
            has_google_mentor_drive_access=True,
            has_google_admin_drive_access=True,
            on_first_website=True,
            signed_first_consent_form=True,
            on_canvas=True,
            has_zoom_account=True,
            in_onshape_classroom=True,
            on_canva=True,
            on_google_mentor_group=True,
            on_google_field_crew_group=True,
            email_updates=True,
        )

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertTrue(keep.on_discord)
        self.assertTrue(keep.has_cmu_id_card)
        self.assertTrue(keep.has_cmu_building_access)
        self.assertTrue(keep.has_google_team_drive_access)
        self.assertTrue(keep.has_google_mentor_drive_access)
        self.assertTrue(keep.has_google_admin_drive_access)
        self.assertTrue(keep.on_first_website)
        self.assertTrue(keep.signed_first_consent_form)
        self.assertTrue(keep.on_canvas)
        self.assertTrue(keep.has_zoom_account)
        self.assertTrue(keep.in_onshape_classroom)
        self.assertTrue(keep.on_canva)
        self.assertTrue(keep.on_google_mentor_group)
        self.assertTrue(keep.on_google_field_crew_group)
        self.assertTrue(keep.email_updates)

    def test_merge_copies_missing_photo(self):
        keep = self._parent("Jane", "Doe")
        img_io = io.BytesIO()
        img = Image.new("RGB", (10, 10), color="blue")
        img.save(img_io, format="JPEG")
        img_io.seek(0)
        photo = SimpleUploadedFile(
            "avatar.jpg", img_io.getvalue(), content_type="image/jpeg"
        )
        source = self._parent("Janet", "Doe", photo=photo)

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertTrue(bool(keep.photo))

    def test_merge_transfers_alumni_student_record(self):
        student = self._student()
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe", student_record=student)

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.student_record, student)

    def test_merge_transfers_andrew_id_sponsor_fields(self):
        sponsor_adult = self._parent("Sponsor", "Mentor", is_mentor=True)
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe", andrew_id_sponsor=sponsor_adult)

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.andrew_id_sponsor, sponsor_adult)

    def test_merge_handles_self_referential_sponsor(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")
        source.andrew_id_sponsor = source
        source.save()

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.andrew_id_sponsor, keep)

    def test_merge_updates_sponsored_adults_and_students(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")

        sponsored_adult = self._parent("Mentee", "One", andrew_id_sponsor=source)
        sponsored_student = self._student(andrew_id_sponsor=source)

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        sponsored_adult.refresh_from_db()
        sponsored_student.refresh_from_db()
        self.assertEqual(sponsored_adult.andrew_id_sponsor, keep)
        self.assertEqual(sponsored_student.andrew_id_sponsor, keep)

    def test_merge_transfers_and_merges_background_checks(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")

        # Source has a state_police check and an FBI check
        today = timezone.localdate()
        BackgroundCheck.objects.create(
            adult=source,
            check_type="fbi",
            cleared=True,
            obtained_date=today,
        )
        BackgroundCheck.objects.create(
            adult=source,
            check_type="state_police",
            cleared=True,
            obtained_date=today,
        )

        # Keep already has an uncleared state_police check without obtained date
        BackgroundCheck.objects.create(
            adult=keep,
            check_type="state_police",
            cleared=False,
            obtained_date=None,
        )

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertEqual(BackgroundCheck.objects.filter(adult=keep).count(), 2)
        sp_check = BackgroundCheck.objects.get(adult=keep, check_type="state_police")
        self.assertTrue(sp_check.cleared)
        self.assertEqual(sp_check.obtained_date, today)

        fbi_check = BackgroundCheck.objects.get(adult=keep, check_type="fbi")
        self.assertTrue(fbi_check.cleared)
        self.assertEqual(fbi_check.obtained_date, today)

    def test_merge_transfers_mentor_agreements_and_submissions(self):
        agreement1 = MentorAgreement.objects.create(
            slug="code-of-conduct",
            title="Code of Conduct",
            version=1,
            effective_date=timezone.localdate(),
            is_active=True,
        )
        agreement2 = MentorAgreement.objects.create(
            slug="safety-agreement",
            title="Safety Agreement",
            version=1,
            effective_date=timezone.localdate(),
            is_active=True,
        )

        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")

        # Keep accepted agreement1; Source accepted agreement2 and has a submission
        MentorAgreementAcceptance.objects.create(adult=keep, agreement=agreement1)
        MentorAgreementAcceptance.objects.create(adult=source, agreement=agreement2)
        sub_file = SimpleUploadedFile(
            "signed.pdf", b"%PDF-1.4", content_type="application/pdf"
        )
        MentorAgreementSubmission.objects.create(
            adult=source, agreement=agreement2, file=sub_file
        )

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertEqual(
            MentorAgreementAcceptance.objects.filter(adult=keep).count(), 2
        )
        self.assertTrue(
            MentorAgreementAcceptance.objects.filter(
                adult=keep, agreement=agreement2
            ).exists()
        )
        self.assertEqual(
            MentorAgreementSubmission.objects.filter(adult=keep).count(), 1
        )
        self.assertTrue(
            MentorAgreementSubmission.objects.filter(
                adult=keep, agreement=agreement2
            ).exists()
        )

    def test_merge_transfers_outreach_mentor_signups(self):
        program = Program.objects.create(
            name="Outreach Prog", start_date=timezone.localdate()
        )
        event = OutreachEvent.objects.create(
            program=program, name="Maker Faire", location_name="Convention Ctr"
        )
        shift = OutreachShift.objects.create(
            event=event,
            date=timezone.localdate(),
            start_time=datetime.time(10, 0),
            end_time=datetime.time(14, 0),
        )

        keep = self._parent("Jane", "Doe", is_mentor=True)
        source = self._parent("Janet", "Doe", is_mentor=True)
        OutreachMentorSignup.objects.create(adult=source, shift=shift)

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertTrue(
            OutreachMentorSignup.objects.filter(adult=keep, shift=shift).exists()
        )

    def test_merge_transfers_sliding_scale_applications(self):
        student = self._student()
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")

        app = SlidingScale.objects.create(
            student=student,
            applied_by=source,
            family_size=4,
            adjusted_gross_income=50000,
        )

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        app.refresh_from_db()
        self.assertEqual(app.applied_by, keep)

    def test_merge_transfers_attendance_sessions_and_events(self):
        program = Program.objects.create(
            name="Attendance Prog", start_date=timezone.localdate()
        )
        keep = self._parent("Jane", "Doe", is_mentor=True)
        source = self._parent("Janet", "Doe", is_mentor=True)

        session = AttendanceSession.objects.create(
            program=program, adult=source, check_in=timezone.now()
        )
        event = AttendanceEvent.objects.create(
            program=program,
            adult=source,
            event_type=AttendanceEvent.IN,
        )

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        session.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(session.adult, keep)
        self.assertEqual(event.adult, keep)

    def test_merge_transfers_rfid_cards(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")
        RFIDCard.objects.create(adult=source, uid="CARD-123456")

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertTrue(RFIDCard.objects.filter(adult=keep, uid="CARD-123456").exists())

    def test_merge_preserves_existing_keep_data_over_source(self):
        keep = self._parent(
            "Jane",
            "Doe",
            personal_email="keep@example.com",
            employer="Existing Employer",
            notes="Keep Notes",
            start_year=2020,
        )
        source = self._parent(
            "Janet",
            "Doe",
            personal_email="source@example.com",
            employer="Source Employer",
            notes="Source Notes",
            start_year=2023,
        )

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.personal_email, "keep@example.com")
        self.assertEqual(keep.employer, "Existing Employer")
        self.assertEqual(keep.notes, "Keep Notes")
        self.assertEqual(keep.start_year, 2020)
