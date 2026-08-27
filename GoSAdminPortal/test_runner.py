"""Custom Django test runner used by the GoS Admin Portal.

The default ``DiscoverRunner`` lets Django deprecation warnings accumulate
silently until a version bump forces a painful cleanup. This runner escalates
Django's "removed in the next version" warnings to errors so deprecated usage
fails the build immediately.
"""

import warnings

from django.test.runner import DiscoverRunner
from django.utils.deprecation import (
    RemovedInDjango70Warning,
    RemovedInNextVersionWarning,
)


class UpgradeAwareTestRunner(DiscoverRunner):
    """DiscoverRunner that escalates Django deprecation warnings to errors."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        warnings.simplefilter("error", RemovedInNextVersionWarning)
        warnings.simplefilter("error", RemovedInDjango70Warning)
