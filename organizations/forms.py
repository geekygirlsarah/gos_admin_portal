"""Form mixins and utilities for multi-tenant organization scoping."""


def limit_choices_to_organization(form, organization):
    """Scope all ModelChoiceField and ModelMultipleChoiceField querysets on a form to ``organization``.

    If ``organization`` is None, the querysets remain un-scoped (returning all choices or
    transition unscoped choices as defined by the manager).
    """
    if organization is None:
        return form

    for field in form.fields.values():
        if hasattr(field, "queryset") and field.queryset is not None:
            qs = field.queryset
            if hasattr(qs, "for_organization") and callable(qs.for_organization):
                field.queryset = qs.for_organization(organization)
    return form


class OrganizationFormMixin:
    """Mixin for Django forms and ModelForms to scope choice querysets to an organization.

    Accepts an optional ``organization`` keyword argument during initialization.
    For every field with a queryset (ModelChoiceField, ModelMultipleChoiceField),
    if the underlying queryset supports ``.for_organization()``, it automatically
    restricts the choices to the provided organization.
    """

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop("organization", None)
        super().__init__(*args, **kwargs)
        if self.organization is not None:
            limit_choices_to_organization(self, self.organization)
