"""Custom Django test runner used by the GoS Admin Portal.

The default ``DiscoverRunner`` lets Django deprecation warnings accumulate
silently until a version bump forces a painful cleanup. This runner escalates
Django's "removed in the next version" warnings to errors so deprecated usage
fails the build immediately.
"""

import os
import warnings

from django.conf import settings
from django.test.runner import DiscoverRunner
from django.utils.deprecation import (
    RemovedInDjango70Warning,
    RemovedInNextVersionWarning,
)

_default_org_id = None


def _assign_default_test_org(sender, instance, **kwargs):
    """Give new org-owned instances created without an organization the seeded GoS org.

    ``for_organization`` no longer returns unscoped (NULL) rows, but many older
    tests predate organizations and create rows without one. This test-only
    shim keeps them scoped to the seeded org. Tests that need a genuinely
    unscoped row should create it normally and then clear it with
    ``Model.objects.filter(pk=...).update(organization=None)``. New tests
        should pass ``organization=`` explicitly.
    """
    global _default_org_id
    if instance.pk is not None or instance.organization_id is not None:
        return
    if _default_org_id is None:
        from organizations.models import Organization

        _default_org_id = (
            Organization.objects.filter(slug="gos").values_list("pk", flat=True).first()
        )
    if _default_org_id is not None:
        instance.organization_id = _default_org_id


def install_default_test_org_hook():
    """Connect the default-org shim for every org-owned model."""
    from django.apps import apps
    from django.db.models.signals import post_init

    from organizations.models import OrganizationQuerySet

    for model in apps.get_models():
        manager = getattr(model, "objects", None)
        if (
            manager is not None
            and any(f.name == "organization" for f in model._meta.fields)
            and isinstance(manager.get_queryset(), OrganizationQuerySet)
        ):
            post_init.connect(
                _assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            )


class UpgradeAwareTestRunner(DiscoverRunner):
    """DiscoverRunner that escalates Django deprecation warnings to errors."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        # Inherited by spawned parallel workers, whose AppConfig.ready() installs the hook.
        os.environ["MOMENTUM_TEST_DEFAULT_ORG"] = "1"
        install_default_test_org_hook()
        # The test runner forces DEBUG=False, so WhiteNoise stops using
        # "autorefresh" mode and warns whenever STATIC_ROOT doesn't exist yet
        # (CI checkouts have no collected static files). Ensure the directory
        # is present so the middleware doesn't emit "No directory at: ..."
        # warnings on every request.
        if settings.STATIC_ROOT:
            os.makedirs(settings.STATIC_ROOT, exist_ok=True)
        warnings.simplefilter("error", RemovedInNextVersionWarning)
        warnings.simplefilter("error", RemovedInDjango70Warning)
