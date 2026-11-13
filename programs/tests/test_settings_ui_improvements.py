import datetime
import io

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from organizations.models import Organization, OrganizationDomain
from programs.models import (
    Adult,
    Crew,
    Fee,
    MentorAgreement,
    Program,
    ProgramDocument,
    ProgramFeature,
    RolePermission,
    SlidingScale,
    SlidingScaleSettings,
    Student,
    SubTeam,
    Team,
)


class SettingsUIImprovementsTests(TestCase):
    def setUp(self):
        lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user = User.objects.create_superuser(
            username="lead_mentor",
            email="lead@example.com",
            password="password123",  # nosec B106
        )
        self.lead_user.groups.add(lead_mentor_group)

        # Standard mentor
        mentor_group, _ = Group.objects.get_or_create(name="Mentor")
        self.mentor_user = User.objects.create_user(
            username="regular_mentor",
            email="mentor@example.com",
            password="password123",  # nosec B106
        )
        self.mentor_user.groups.add(mentor_group)

        # Get or create features
        self.feat_orders, _ = ProgramFeature.objects.get_or_create(
            key="orders", defaults={"name": "Orders", "display_order": 1}
        )
        self.feat_attendance, _ = ProgramFeature.objects.get_or_create(
            key="attendance", defaults={"name": "Attendance", "display_order": 2}
        )

        # Create program
        self.program = Program.objects.create(
            name="Girls of Steel FRC",
            description="High school robotics competition team.",
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2027, 5, 30),
            applications_open=datetime.date(2026, 8, 1),
            applications_close=datetime.date(2026, 9, 15),
            grade_range_start=9,
            grade_range_end=12,
            cost="$350",
            active=True,
        )
        self.program.features.add(self.feat_orders, self.feat_attendance)

        # Create team, crew, subteam
        self.team = Team.objects.create(
            team_type="FRC", number=3504, name="Girls of Steel", color="#e83e8c"
        )
        self.crew = Crew.objects.create(
            program=self.program, name="Chassis & Drivetrain", color="#0d6efd"
        )
        self.subteam = SubTeam.objects.create(
            program=self.program, name="Mechanical", color="#198754"
        )

    def _login(self, user):
        self.client.force_login(user)

    def test_portal_settings_page_ui_and_sections(self):
        """Portal Settings should render all categorized tabs, permissions, teams, and imports cleanly."""
        self._login(self.lead_user)
        url = reverse("portal_settings")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "teams")

        # Verify page header and title
        self.assertContains(response, "Portal Settings")

        # Verify all tabs exist
        self.assertContains(response, 'id="permissions-tab"')
        self.assertContains(response, 'id="portal_address-tab"')
        self.assertContains(response, 'id="branding-tab"')
        self.assertContains(response, 'id="teams-tab"')
        self.assertContains(response, 'id="crews-tab"')
        self.assertContains(response, 'id="subteams-tab"')
        self.assertContains(response, 'id="imports-tab"')
        self.assertContains(response, 'id="kiosk_configs-tab"')
        self.assertContains(response, 'id="sliding_scale_settings-tab"')
        self.assertContains(response, 'id="agreements-tab"')
        tab_order = [
            "teams",
            "crews",
            "subteams",
            "lead_mentors",
            "kiosk_configs",
            "imports",
            "sliding_scale_settings",
            "payment_instructions",
            "agreements",
            "background_checks",
            "org_accounts",
            "portal_address",
            "branding",
            "email",
            "permissions",
        ]
        tab_positions = [
            response.content.index(f'id="{tab_id}-tab"'.encode())
            for tab_id in tab_order
        ]
        self.assertEqual(tab_positions, sorted(tab_positions))

        permissions_response = self.client.get(url, {"tab": "permissions"})
        self.assertEqual(permissions_response.context["active_tab"], "permissions")

        # Verify role headers and permission controls
        self.assertContains(response, "Lead Mentor")
        self.assertContains(response, "Mentor")
        self.assertContains(response, "Full Access")
        self.assertContains(response, "None: No Access")
        self.assertContains(response, "Read: View Only")
        self.assertContains(response, "R / W: Full Management")
        self.assertContains(response, "Save Permissions")

        # Verify teams tab items
        self.assertContains(response, "3504")
        self.assertContains(response, "Girls of Steel")
        self.assertContains(response, "Add New Team")

        # Verify imports tab cards
        self.assertContains(response, "Students")
        self.assertContains(response, "Parents")
        self.assertContains(response, "Student-Parent Relationships")
        self.assertContains(response, "Mentors")
        self.assertContains(response, "Schools")
        self.assertContains(response, "Attendance")

        # Verify CSP nonce in script tags
        self.assertIn("csp_nonce", response.context)
        self.assertContains(response, f'nonce="{response.context["csp_nonce"]}"')

    @override_settings(MOMENTUM_ROOT_DOMAIN="momentumteam.app")
    def test_portal_address_tab_shows_momentum_address_and_setup_guidance(self):
        org, _ = Organization.objects.get_or_create(
            slug="gos", defaults={"name": "Girls of Steel Robotics"}
        )
        org.subdomain = "gos"
        org.save(update_fields=["subdomain"])
        OrganizationDomain.objects.create(
            organization=org,
            hostname="gos-portal.example.org",
        )
        self._login(self.lead_user)

        response = self.client.get(reverse("portal_settings") + "?tab=portal_address")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your Momentum portal address")
        self.assertContains(response, 'href="https://gos.momentumteam.app"')
        self.assertContains(
            response,
            "Your Momentum address keeps working after you connect a domain.",
        )
        self.assertContains(response, "Request a domain you own")
        self.assertContains(response, "Check TXT record")
        self.assertContains(
            response,
            "Your portal address and your email sending domain are configured separately.",
        )

    def test_crews_and_subteams_grouped_by_program(self):
        """Portal Settings should render Crews and SubTeams grouped into cards by Program with inline quick-add forms."""
        self._login(self.lead_user)
        url = reverse("portal_settings")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Crews tab checks
        self.assertContains(response, "Girls of Steel FRC")
        self.assertContains(response, "Chassis &amp; Drivetrain")
        self.assertContains(response, "1 Crew")
        self.assertContains(response, "New crew name for Girls of Steel FRC...")

        # Subteams tab checks
        self.assertContains(response, "Mechanical")
        self.assertContains(response, "1 SubTeam")
        self.assertContains(response, "New subteam name for Girls of Steel FRC...")

    def test_student_and_mentor_photo_crop_assets_rendering(self):
        """Photo crop modal renders local Cropper assets with a showing crop box."""
        student = Student.objects.create(
            legal_first_name="Jane",
            last_name="Doe",
            graduation_year=2028,
        )
        self._login(self.lead_user)

        # Student create + edit forms
        resp_create = self.client.get(reverse("student_create"))
        resp_student = self.client.get(reverse("student_edit", args=[student.pk]))
        for resp in (resp_create, resp_student):
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "Crop Photo")
            # CropperJS must be served from local static files so it can never be
            # blocked by CSP or CDN availability (previously gray modal + dead JS).
            self.assertContains(
                resp,
                'src="/static/vendor/cropperjs/1.6.2/cropper.min.js"',
            )
            self.assertContains(
                resp,
                'href="/static/vendor/cropperjs/1.6.2/cropper.min.css"',
            )
            # The crop image must NOT be wrapped in Bootstrap's .ratio. CropperJS
            # positions its container with inline `position: relative`, which
            # overrides `.ratio > *`'s absolute positioning and pushes the crop
            # area below the visible modal (gray box, unclickable confirm).
            self.assertNotContains(resp, 'class="ratio ratio-1x1 bg-light"')
            self.assertContains(resp, 'class="cropper-wrap"')
            # The modal dialog must NOT be tagged `cropper-modal`: CropperJS ships
            # an unscoped `.cropper-modal { background-color:#000; opacity:.5 }`
            # rule, which would render the entire dialog 50% transparent black
            # (whole screen gray, UI grayed out) by class-name collision.
            self.assertNotContains(resp, '"cropper-modal"')
            self.assertContains(
                resp,
                'class="modal-dialog modal-lg modal-dialog-centered crop-modal-dialog"',
            )

        # Adult forms (mentors/parents/adults edit their profile here) must offer
        # the same photo cropping instead of a bare file input.
        resp_adult = self.client.get(reverse("adult_create"))
        self.assertEqual(resp_adult.status_code, 200)
        self.assertContains(resp_adult, "Crop Photo")
        self.assertContains(
            resp_adult,
            'src="/static/vendor/cropperjs/1.6.2/cropper.min.js"',
        )
        self.assertContains(
            resp_adult,
            'href="/static/vendor/cropperjs/1.6.2/cropper.min.css"',
        )
        self.assertContains(resp_adult, 'class="cropper-wrap"')
        self.assertNotContains(resp_adult, '"cropper-modal"')
        self.assertContains(
            resp_adult,
            'class="modal-dialog modal-lg modal-dialog-centered crop-modal-dialog"',
        )

    def test_program_edit_settings_ui(self):
        """Program edit form should render structured cards for identity, schedule, eligibility, and features."""
        self._login(self.lead_user)
        url = reverse("program_edit", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Edit Program")
        self.assertContains(response, "Girls of Steel FRC")
        self.assertContains(response, "High school robotics competition team.")
        self.assertContains(response, "Order Requests")
        self.assertContains(response, "Attendance")
        self.assertContains(response, "Save")

    def test_program_create_ui(self):
        """Program create form should render structured cards cleanly."""
        self._login(self.lead_user)
        url = reverse("program_create")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Create a New Program")
        self.assertContains(response, "Save")

    def test_program_fee_management_ui(self):
        """Program fee select page should render modern fee cards and add buttons."""
        Fee.objects.create(
            program=self.program,
            name="Fall Registration Fee",
            amount="250.00",
            effective_date=datetime.date(2026, 9, 15),
            due_date=datetime.date(2026, 10, 1),
        )
        self._login(self.lead_user)
        url = reverse("program_fee_select", args=[self.program.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Manage Fees")
        self.assertContains(response, "Fall Registration Fee")
        self.assertContains(response, "$250.00")
        self.assertContains(response, "Add Fee")

    def test_sliding_scale_review_list_ui(self):
        """Sliding scale review list should render modern queue cards."""
        self._login(self.lead_user)
        url = reverse("sliding_scale_review_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Sliding Scale Applications")
        self.assertContains(response, "Pending Review")
        self.assertContains(response, "Active Sliding Scales")

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        }
    )
    def test_portal_settings_branding_logo_upload_valid_and_path_traversal(self):
        """Uploading logo validates image format and rejects path traversal or invalid files."""
        self._login(self.lead_user)
        url = reverse("portal_email_settings")

        # 1. Valid PNG upload
        img_byte_arr = io.BytesIO()
        img = Image.new("RGBA", (100, 100), (255, 0, 0, 0))
        img.save(img_byte_arr, format="PNG")
        img_byte_arr.seek(0)
        valid_file = SimpleUploadedFile(
            "logo.png", img_byte_arr.getvalue(), content_type="image/png"
        )

        resp = self.client.post(
            url,
            {
                "action": "update_branding",
                "organization_name": "Test Org",
                "logo_file": valid_file,
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp, "Email branding &amp; template settings updated successfully."
        )
        self.assertIn("tab=branding", resp.redirect_chain[-1][0])

        # 2. Invalid / malicious non-image file with traversal attempts
        invalid_file = SimpleUploadedFile(
            "../../etc/passwd.png",
            b"not a valid image content",
            content_type="image/png",
        )
        resp_invalid = self.client.post(
            url,
            {
                "action": "update_branding",
                "organization_name": "Test Org",
                "logo_file": invalid_file,
            },
            follow=True,
        )
        self.assertEqual(resp_invalid.status_code, 200)
        self.assertContains(resp_invalid, "Invalid logo image file")
