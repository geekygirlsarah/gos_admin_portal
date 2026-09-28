"""The Tiptap email editor bundle must exist for the compose pages to work.

Development (``DEBUG=True``) uses the default static storage where
``{% static %}`` does not verify that a file exists, so a clobbered bundle
would silently render all three compose pages without an editor. CI and the
Render build both run ``npm ci && npm run build`` (see ``run_ci.sh``,
``run_ci.ps1`` and ``build.sh``); this test guards that locally too.
"""

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase


class EmailEditorBundleTests(SimpleTestCase):
    def test_editor_bundle_is_builable_and_found(self):
        path = finders.find("email-editor.js")
        self.assertIsNotNone(
            path,
            "The email editor bundle is missing. Run `npm ci && npm run build` "
            "and commit the lockfile update.",
        )

    def test_editor_stylesheet_is_found(self):
        path = finders.find("css/email_editor.css")
        self.assertIsNotNone(path)
