"""Tests for the ``find_disassociated_parents`` management command.

The command looks through converted applications and compares the parents
captured in the wizard (Step 7 primary / Step 8 secondary) against the links
on the resulting Student record. Old wizard bugs could create a parent Adult
record during conversion but fail to link it to the student, or link it so the
student's primary/secondary contact pointers never point at the right person.

It is read-only by default; ``--fix`` re-links the unambiguous cases (exactly
one Adult matches the application's parent email and the student has no such
link), never creating new Adult records and never overriding an existing
contact pointer. ``--all`` is needed to include fully-OK applications in the
report (by default only applications with at least one issue are shown).
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from applications.models import Application
from audit.events import AuditEvent
from audit.models import AuditLog
from programs.models import Adult, AdultStudentRelationship, Program, Student


class FindDisassociatedParentsTests(TestCase):
    def _student(self, first="Jordan", last="Smith"):
        return Student.objects.create(
            legal_first_name=first,
            last_name=last,
            graduation_year=2027,
        )

    def _adult(self, first="Jane", last="Doe", email="jane@example.com", **kwargs):
        defaults = {"is_parent": True, "login_enabled": True}
        defaults.update(kwargs)
        return Adult.objects.create(
            legal_first_name=first,
            last_name=last,
            personal_email=email,
            **defaults,
        )

    def _app(self, student, step5=None, step7=None, step8=None, **kwargs):
        program = kwargs.pop("program", None) or Program.objects.create(
            name="Summer Program", start_date=timezone.localdate()
        )
        data = {}
        if step5 is not None:
            data["step5-student"] = step5
        if step7 is not None:
            data["step7-primaryparent"] = step7
        if step8 is not None:
            data["step8-secondaryparent"] = step8
        return Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            program=program,
            status=Application.Status.CONVERTED,
            converted_student=student,
            data=data,
            email=kwargs.pop("email", "student@example.com"),
            **kwargs,
        )

    def _link(self, adult, student):
        return AdultStudentRelationship.objects.create(adult=adult, student=student)

    def _run(self, fix=False, all_=False):
        out = StringIO()
        call_command("find_disassociated_parents", fix=fix, all=all_, stdout=out)
        return out.getvalue()

    def _set_primary(self, student, relationship):
        student.primary_contact_relationship = relationship
        student.save(update_fields=["primary_contact_relationship"])

    def _set_secondary(self, student, relationship):
        student.secondary_contact_relationship = relationship
        student.save(update_fields=["secondary_contact_relationship"])

    # --- OK / clean ----------------------------------------------------------

    def test_clean_database_reports_success(self):
        output = self._run()
        self.assertIn("No disassociated parents found", output)

    def test_linked_parent_matching_application_is_ok(self):
        student = self._student()
        parent = self._adult("Jane", "Smith", email="jane.smith@example.com")
        rel = self._link(parent, student)
        self._set_primary(student, rel)
        self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "JANE.SMITH@example.com",
            },
        )

        output = self._run()

        self.assertIn("No disassociated parents found", output)
        self.assertIn("OK", self._run(all_=True))

    def test_ok_applications_hidden_by_default_shown_with_all(self):
        student = self._student()
        parent = self._adult("Jane", "Smith", email="jane@example.com")
        rel = self._link(parent, student)
        self._set_primary(student, rel)
        app = self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "jane@example.com",
            },
        )

        self.assertNotIn(app.application_id, self._run())
        self.assertIn(app.application_id, self._run(all_=True))

    # --- disassociated -------------------------------------------------------

    def test_orphaned_parent_record_reported_as_disassociated(self):
        student = self._student()
        orphan = self._adult("Jane", "Smith", email="jane.smith@example.com")
        app = self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "jane.smith@example.com",
            },
        )

        output = self._run()

        self.assertIn(app.application_id, output)
        self.assertIn("DISASSOCIATED", output)
        self.assertIn(f"#{orphan.pk}", output)

    def test_missing_parent_record_reported_when_no_adult_matches(self):
        student = self._student()
        self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "nobody@example.com",
            },
        )

        output = self._run()

        self.assertIn("MISSING", output)
        self.assertIn("nobody@example.com", output)

    def test_report_shows_relationship_and_specific_type(self):
        student = self._student()
        self._adult("Jane", "Smith", email="jane@example.com")
        app = self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "jane@example.com",
                "relationship_to_student": "parent",
                "specific_relationship": "grandfather",
            },
        )

        output = self._run()

        self.assertIn(app.application_id, output)
        self.assertIn("relationship: parent/grandfather", output)

    def test_report_omits_relationship_when_application_has_none(self):
        student = self._student()
        orphan = self._adult("Jane", "Smith", email="jane@example.com")
        self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "jane@example.com",
            },
        )

        output = self._run()

        self.assertIn(f"#{orphan.pk}", output)
        self.assertNotIn("relationship:", output)

    def test_shared_email_multiple_adults_reported_ambiguous(self):
        """Two duplicate records with the same name AND email stay ambiguous."""
        student = self._student()
        self._adult("Jane", "Smith", email="family@example.com")
        self._adult("Jane", "Smith", email="family@example.com")
        self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "family@example.com",
            },
        )

        output = self._run()

        self.assertIn("AMBIGUOUS", output)
        self.assertIn("family@example.com", output)

    def test_shared_email_single_name_match_is_disassociated(self):
        """A unique well-named record among email-shares is the clear parent."""
        student = self._student()
        jane = self._adult("Jane", "Smith", email="family@example.com")
        self._adult("John", "Smith", email="family@example.com")
        self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "family@example.com",
            },
        )

        output = self._run()

        self.assertIn("DISASSOCIATED", output)
        self.assertIn(f"#{jane.pk}", output)

    def test_shared_family_email_not_flagged_as_role_mismatch(self):
        """Both app parents on one email, one linked: the email match must not
        blame the linked parent for the *other* parent's missing slot.

        Regression: the app's primary was "Pushkarraj <dr.deshmukh@gmail.com>";
        only the secondary "Swati <dr.deshmukh@gmail.com>" ever converted and
        is linked. An email-only match used to flag Swati as ROLE_MISMATCH for
        the primary slot, even though she is a different (correct) person.
        """
        student = self._student()
        swati = self._adult("Swati", "Suryawanshi", email="dr.deshmukh@gmail.com")
        rel = self._link(swati, student)
        self._set_secondary(student, rel)
        app = self._app(
            student,
            step7={
                "legal_first_name": "Pushkarraj",
                "last_name": "Deshmukh",
                "email": "dr.deshmukh@gmail.com",
            },
            step8={
                "legal_first_name": "Swati",
                "last_name": "Suryawanshi",
                "email": "",
            },
        )

        output = self._run()

        self.assertNotIn("ROLE_MISMATCH", output)
        self.assertNotIn("linked but not set", output)
        self.assertIn("MISSING", output)
        self.assertIn(f"#{swati.pk}", output)
        self.assertIn(app.application_id, output)
        self.assertIn("OK", output)

    def test_shared_email_with_separate_unlinked_record_is_disassociated(self):
        """Both parents converted separately but share an email; the orphaned
        primary (matched by name) is still safely auto-linkable."""
        student = self._student()
        swati = self._adult("Swati", "Suryawanshi", email="dr.deshmukh@gmail.com")
        pushkarraj = self._adult(
            "Pushkarraj", "Deshmukh", email="dr.deshmukh@gmail.com"
        )
        rel = self._link(swati, student)
        self._set_secondary(student, rel)
        self._app(
            student,
            step7={
                "legal_first_name": "Pushkarraj",
                "last_name": "Deshmukh",
                "email": "dr.deshmukh@gmail.com",
            },
        )

        output = self._run()

        self.assertIn("DISASSOCIATED", output)
        self.assertIn(f"#{pushkarraj.pk}", output)

    def test_parent_linked_but_not_in_expected_slot_reported(self):
        student = self._student()
        parent = self._adult("Jane", "Smith", email="jane@example.com")
        rel = self._link(parent, student)
        # Expected primary, but linked only as a plain parent (no slot).
        self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "jane@example.com",
            },
        )
        self.assertTrue(rel.pk)

        output = self._run()

        self.assertIn("ROLE", output)

    def test_legacy_step_keys_are_normalized(self):
        student = self._student()
        orphan = self._adult("Jane", "Smith", email="jane@example.com")
        program = Program.objects.create(name="Legacy", start_date=timezone.localdate())
        app = Application.objects.create(
            applicant_type=Application.Type.STUDENT,
            program=program,
            status=Application.Status.CONVERTED,
            converted_student=student,
            data={
                "step7": {
                    "legal_first_name": "Jane",
                    "last_name": "Smith",
                    "email": "jane@example.com",
                }
            },
            email="student@example.com",
        )

        output = self._run()

        self.assertIn(app.application_id, output)
        self.assertIn(f"#{orphan.pk}", output)

    def test_secondary_skipped_is_not_reported(self):
        student = self._student()
        parent = self._adult("Jane", "Smith", email="jane@example.com")
        rel = self._link(parent, student)
        self._set_primary(student, rel)
        self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "jane@example.com",
            },
            step8={"_skipped": True},
        )

        output = self._run()

        self.assertIn("No disassociated parents found", output)

    def test_secondary_parent_reported_independently(self):
        student = self._student()
        orphan = self._adult("John", "Smith", email="john@example.com")
        app = self._app(
            student,
            step8={
                "legal_first_name": "John",
                "last_name": "Smith",
                "email": "john@example.com",
            },
        )

        output = self._run()

        self.assertIn(app.application_id, output)
        self.assertIn("Secondary", output)
        self.assertIn(f"#{orphan.pk}", output)

    # --- --fix ----------------------------------------------------------------

    def test_fix_links_orphaned_parent(self):
        student = self._student()
        orphan = self._adult("Jane", "Smith", email="jane@example.com")
        self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "jane@example.com",
            },
        )

        self._run(fix=True)

        student.refresh_from_db()
        self.assertTrue(orphan in student.adults.all())
        self.assertEqual(student.primary_contact_id, orphan.pk)

    def test_fix_holds_relationship_details_from_application(self):
        student = self._student()
        orphan = self._adult("Jane", "Smith", email="jane@example.com")
        self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "jane@example.com",
                "relationship_to_student": "parent",
                "specific_relationship": "mother",
            },
        )

        self._run(fix=True)

        rel = AdultStudentRelationship.objects.get(adult=orphan, student=student)
        self.assertEqual(rel.specific_relationship, "mother")

    def test_fix_links_secondary_parent(self):
        student = self._student()
        orphan = self._adult("John", "Smith", email="john@example.com")
        self._app(
            student,
            step8={
                "legal_first_name": "John",
                "last_name": "Smith",
                "email": "john@example.com",
            },
        )

        self._run(fix=True)

        student.refresh_from_db()
        self.assertTrue(orphan in student.adults.all())
        self.assertEqual(student.secondary_contact_id, orphan.pk)

    def test_fix_does_not_override_existing_different_primary(self):
        student = self._student()
        existing = self._adult("Deb", "Smith", email="deb@example.com")
        rel = self._link(existing, student)
        self._set_primary(student, rel)
        # Application says Jane should be the primary; Jane exists as an
        # orphan record. We must NOT yank the current primary away.
        orphan = self._adult("Jane", "Smith", email="jane@example.com")
        self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "jane@example.com",
            },
        )

        self._run(fix=True)

        student.refresh_from_db()
        self.assertEqual(student.primary_contact_id, existing.pk)
        self.assertTrue(
            AdultStudentRelationship.objects.filter(
                adult=orphan, student=student
            ).exists()
        )

    def test_fix_does_nothing_when_name_is_ambiguous(self):
        """Two records with the same name (duplicates) are never auto-linked."""
        student = self._student()
        a = self._adult("Jane", "Smith", email="family@example.com")
        b = self._adult("Jane", "Smith", email="family@example.com")
        self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "family@example.com",
            },
        )

        self._run(fix=True)

        self.assertFalse(
            AdultStudentRelationship.objects.filter(
                adult__in=[a, b], student=student
            ).exists()
        )

    def test_fix_links_unique_name_match_among_shared_email(self):
        """A unique name match auto-links even when the email is a shared
        family address."""
        student = self._student()
        jane = self._adult("Jane", "Smith", email="family@example.com")
        self._adult("John", "Smith", email="family@example.com")
        self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "family@example.com",
            },
        )

        self._run(fix=True)

        student.refresh_from_db()
        self.assertTrue(jane in student.adults.all())
        self.assertEqual(student.primary_contact_id, jane.pk)

    def test_fix_logs_audit_event(self):
        student = self._student()
        orphan = self._adult("Jane", "Smith", email="jane@example.com")
        self._app(
            student,
            step7={
                "legal_first_name": "Jane",
                "last_name": "Smith",
                "email": "jane@example.com",
            },
        )

        self._run(fix=True)

        audit = AuditLog.objects.filter(
            event=AuditEvent.GUARDIAN_ADDED,
            resource_type="Student",
            resource_id=str(student.pk),
        ).first()
        self.assertIsNotNone(audit)
        self.assertIn(str(orphan.pk), audit.notes)
