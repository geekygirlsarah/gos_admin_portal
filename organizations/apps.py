from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "organizations"
    verbose_name = "Organizations"

    def ready(self):
        import os

        # Set only by the test runner; lets spawned parallel workers install
        # the transitional default-org shim (see GoSAdminPortal/test_runner.py).
        if os.environ.get("MOMENTUM_TEST_DEFAULT_ORG") == "1":
            from GoSAdminPortal.test_runner import install_default_test_org_hook

            install_default_test_org_hook()
