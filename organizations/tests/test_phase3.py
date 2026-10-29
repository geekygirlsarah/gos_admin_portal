"""Phase 3 tests: Making new features org-aware by default.

Verifies:
1. OrganizationScopeMixin auto-filters querysets in class-based views to request.organization
   and assigns request.organization on instance creation.
2. OrganizationFormMixin and limit_choices_to_organization scope ModelChoiceField
   and ModelMultipleChoiceField querysets to the current organization.
3. Generic model pattern with OrganizationManager / OrganizationQuerySet.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from organizations.forms import (
    OrganizationFormMixin,
    limit_choices_to_organization,
)
from organizations.mixins import OrganizationScopeMixin
from organizations.models import (
    Organization,
    OrganizationManager,
    OrganizationQuerySet,
)
from programs.models import Program, Student


class TestStudentForm(OrganizationFormMixin, forms.Form):
    student = forms.ModelChoiceField(queryset=Student.objects.all())
    programs = forms.ModelMultipleChoiceField(queryset=Program.objects.all())


class TestPlainStudentForm(forms.Form):
    student = forms.ModelChoiceField(queryset=Student.objects.all())


class TestProgramModelForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = Program
        fields = ["name", "description"]


class TestProgramListView(OrganizationScopeMixin, ListView):
    model = Program


class TestProgramDetailView(OrganizationScopeMixin, DetailView):
    model = Program


class TestProgramCreateView(OrganizationScopeMixin, CreateView):
    model = Program
    fields = ["name", "description"]
    success_url = "/success/"


class OrganizationScopeMixinTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other = Organization.objects.create(name="Rival Org", slug="rival")
        self.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="pw"  # nosec B106
        )
        self.rf = RequestFactory()

    def _make_request(self, path="/", org=None):
        req = self.rf.get(path)
        req.user = self.user
        req.organization = org or self.gos
        return req

    def test_list_view_auto_filters_to_request_organization(self):
        p_gos = Program.objects.create(name="GoS 1", organization=self.gos)
        p_unscoped = Program.objects.create(name="Unscoped 1")
        p_other = Program.objects.create(name="Other 1", organization=self.other)

        view = TestProgramListView()
        view.setup(self._make_request("/", org=self.gos))
        qs = view.get_queryset()

        ids = set(qs.values_list("id", flat=True))
        self.assertIn(p_gos.id, ids)
        self.assertIn(p_unscoped.id, ids)
        self.assertNotIn(p_other.id, ids)

    def test_list_view_with_none_organization_returns_all(self):
        p_gos = Program.objects.create(name="GoS 1", organization=self.gos)
        p_other = Program.objects.create(name="Other 1", organization=self.other)

        view = TestProgramListView()
        req = self._make_request("/")
        req.organization = None
        view.setup(req)
        qs = view.get_queryset()

        self.assertEqual(qs.count(), 2)

    def test_detail_view_scopes_queryset(self):
        p_gos = Program.objects.create(name="GoS 1", organization=self.gos)
        p_other = Program.objects.create(name="Other 1", organization=self.other)

        # Accessing GoS program with GoS org succeeds
        view = TestProgramDetailView()
        view.setup(self._make_request("/", org=self.gos), pk=p_gos.pk)
        view.object = view.get_object()
        self.assertEqual(view.object, p_gos)

        # Accessing other org's program with GoS org raises 404
        view_other = TestProgramDetailView()
        view_other.setup(self._make_request("/", org=self.gos), pk=p_other.pk)
        with self.assertRaises(Http404):
            view_other.get_object()

    def test_create_view_auto_assigns_organization_in_form_valid(self):
        req = self.rf.post(
            "/create/", data={"name": "New Program", "description": "Desc"}
        )
        req.user = self.user
        req.organization = self.gos

        view = TestProgramCreateView()
        view.setup(req)
        form = view.get_form()
        self.assertTrue(form.is_valid(), form.errors)
        response = view.form_valid(form)
        self.assertEqual(response.status_code, 302)

        created = Program.objects.get(name="New Program")
        self.assertEqual(created.organization, self.gos)

    def test_get_form_kwargs_passes_organization_when_supported(self):
        view = TestProgramCreateView()
        view.form_class = TestProgramModelForm
        view.setup(self._make_request("/", org=self.gos))
        kwargs = view.get_form_kwargs()
        self.assertEqual(kwargs.get("organization"), self.gos)


class OrganizationFormMixinTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other = Organization.objects.create(name="Rival Org", slug="rival")

        self.s_gos = Student.objects.create(
            legal_first_name="Alice", last_name="Smith", organization=self.gos
        )
        self.s_unscoped = Student.objects.create(
            legal_first_name="Bob", last_name="Jones"
        )
        self.s_other = Student.objects.create(
            legal_first_name="Charlie", last_name="Brown", organization=self.other
        )

        self.p_gos = Program.objects.create(name="GoS Prog", organization=self.gos)
        self.p_other = Program.objects.create(
            name="Other Prog", organization=self.other
        )

    def test_form_limits_choices_to_organization(self):
        form = TestStudentForm(organization=self.gos)

        student_pks = set(form.fields["student"].queryset.values_list("id", flat=True))
        self.assertIn(self.s_gos.id, student_pks)
        self.assertIn(self.s_unscoped.id, student_pks)
        self.assertNotIn(self.s_other.id, student_pks)

        prog_pks = set(form.fields["programs"].queryset.values_list("id", flat=True))
        self.assertIn(self.p_gos.id, prog_pks)
        self.assertNotIn(self.p_other.id, prog_pks)

    def test_form_without_organization_preserves_all_choices(self):
        form = TestStudentForm()
        self.assertEqual(form.fields["student"].queryset.count(), 3)
        self.assertEqual(form.fields["programs"].queryset.count(), 2)

    def test_limit_choices_to_organization_helper(self):
        form = TestPlainStudentForm()
        self.assertEqual(form.fields["student"].queryset.count(), 3)

        limit_choices_to_organization(form, self.gos)
        student_pks = set(form.fields["student"].queryset.values_list("id", flat=True))
        self.assertIn(self.s_gos.id, student_pks)
        self.assertIn(self.s_unscoped.id, student_pks)
        self.assertNotIn(self.s_other.id, student_pks)

    def test_limit_choices_to_organization_with_none_is_noop(self):
        form = TestPlainStudentForm()
        limit_choices_to_organization(form, None)
        self.assertEqual(form.fields["student"].queryset.count(), 3)
