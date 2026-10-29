"""View mixins for multi-tenant organization scoping."""

import inspect


class OrganizationScopeMixin:
    """Mixin for class-based views to automatically scope querysets and forms to the current request's organization.

    Features:
    - Scopes ``get_queryset()``: calls ``qs.for_organization(request.organization)`` if supported.
    - Scopes form kwargs: passes ``organization=request.organization`` in ``get_form_kwargs()``
      if the view's form class accepts it.
    - Sets instance organization: in ``form_valid()``, auto-populates ``form.instance.organization``
      with ``request.organization`` on model forms if the model has an ``organization`` field and
      it is not already set.
    """

    def get_organization(self):
        """Return the active organization from the request."""
        return getattr(getattr(self, "request", None), "organization", None)

    def get_queryset(self):
        """Filter queryset by request.organization if supported."""
        qs = super().get_queryset()
        org = self.get_organization()
        if hasattr(qs, "for_organization") and callable(qs.for_organization):
            return qs.for_organization(org)
        return qs

    def get_form_kwargs(self):
        """Pass organization to the form kwargs if the form accepts it."""
        kwargs = (
            super().get_form_kwargs() if hasattr(super(), "get_form_kwargs") else {}
        )
        org = self.get_organization()
        if org is not None:
            form_class = getattr(self, "form_class", None)
            if (
                form_class is None
                and hasattr(self, "get_form_class")
                and callable(self.get_form_class)
            ):
                try:
                    form_class = self.get_form_class()
                except Exception:  # nosec B110
                    form_class = None
            if form_class is not None:
                try:
                    sig = inspect.signature(form_class.__init__)
                    if "organization" in sig.parameters or any(
                        p.kind == inspect.Parameter.VAR_KEYWORD
                        for p in sig.parameters.values()
                    ):
                        kwargs.setdefault("organization", org)
                except Exception:  # nosec B110
                    pass
        return kwargs

    def form_valid(self, form):
        """Assign request.organization to newly created model instances if applicable."""
        org = self.get_organization()
        if org and hasattr(form, "instance") and hasattr(form.instance, "_meta"):
            try:
                field = form.instance._meta.get_field("organization")
                if field and getattr(form.instance, "organization_id", None) is None:
                    form.instance.organization = org
            except Exception:  # nosec B110
                pass
        if hasattr(super(), "form_valid"):
            return super().form_valid(form)
        return form
