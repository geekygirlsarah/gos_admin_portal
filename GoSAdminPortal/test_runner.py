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


class UpgradeAwareTestRunner(DiscoverRunner):
    """DiscoverRunner that escalates Django deprecation warnings to errors."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        # The test runner forces DEBUG=False, so WhiteNoise stops using
        # "autorefresh" mode and warns whenever STATIC_ROOT doesn't exist yet
        # (CI checkouts have no collected static files). Ensure the directory
        # is present so the middleware doesn't emit "No directory at: ..."
        # warnings on every request.
        if settings.STATIC_ROOT:
            os.makedirs(settings.STATIC_ROOT, exist_ok=True)
        warnings.simplefilter("error", RemovedInNextVersionWarning)
        warnings.simplefilter("error", RemovedInDjango70Warning)
