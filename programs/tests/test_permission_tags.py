from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import TestCase

from organizations.models import Organization, UserOrganizationMembership


class UserRoleTemplateTagTests(TestCase):
    def test_user_role_tag_uses_request_organization(self):
        organization = Organization.objects.create(
            name="Template Role Tenant",
            slug="template-role-tenant",
            subdomain="template-role-tenant",
        )
        user = get_user_model().objects.create_user(username="template-role-lead")
        UserOrganizationMembership.objects.create(
            user=user,
            organization=organization,
            role="LeadMentor",
            is_active=True,
        )
        request = SimpleNamespace(organization=organization)
        template = Template(
            "{% load permission_tags %}{% user_role user as role %}{{ role }}"
        )

        role = template.render(Context({"request": request, "user": user}))

        self.assertEqual(role, "LeadMentor")
