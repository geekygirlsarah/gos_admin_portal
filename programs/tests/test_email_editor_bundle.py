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


class EmailEditorStylesheetTests(SimpleTestCase):
    """A long message must not push the toolbar (and the caret) off-screen."""

    def _css(self) -> str:
        with open(finders.find("css/email_editor.css"), encoding="utf-8") as handle:
            return handle.read()

    def test_toolbar_sticks_to_the_top_of_the_viewport(self):
        css = self._css()
        self.assertRegex(
            css,
            r"\.email-editor \.btn-toolbar\s*\{[^}]*position:\s*sticky",
            "The compose toolbar must stay visible while scrolling a long message.",
        )

    def test_typing_area_is_bounded_and_scrolls_internally(self):
        css = self._css()
        self.assertRegex(
            css,
            r"\.email-editor \.tiptap\s*\{[^}]*max-height:",
            "The typing area must stop growing at a bounded height.",
        )
        self.assertRegex(
            css,
            r"\.email-editor \.tiptap\s*\{[^}]*overflow-y:\s*auto",
            "The typing area must scroll inside itself.",
        )
