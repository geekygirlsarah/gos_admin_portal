from django.forms import Media, widgets
from django.template import Context, Template
from django.test import TestCase

from programs.templatetags.form_tags import media_scripts


class MediaScriptsFilterTest(TestCase):
    def _script_media(self):
        # Django >= 6.2 stores JS media items as Script objects (Django 6.1).
        obj = widgets.Media(js=["js/dual-listbox.js"])
        if obj._js and not isinstance(obj._js[0], str):
            return obj
        return None

    def test_media_scripts_returns_plain_string_paths(self):
        self.assertEqual(
            media_scripts(Media(js=["js/dual-listbox.js"])),
            ["js/dual-listbox.js"],
        )

    def test_media_scripts_handles_script_objects(self):
        media = self._script_media()
        if media is None:
            self.skipTest("This Django version stores JS media as plain strings")

        result = media_scripts(media)

        self.assertEqual(result, ["js/dual-listbox.js"])
        for item in result:
            self.assertIsInstance(item, str)

    def test_media_scripts_used_with_static_tag_renders(self):
        template_content = (
            "{% load form_tags static %}"
            "{% for js in media|media_scripts %}"
            '<script src="{% static js %}"></script>'
            "{% endfor %}"
        )
        media = Media(js=["js/dual-listbox.js"])
        rendered = Template(template_content).render(Context({"media": media}))

        self.assertIn('src="/static/js/dual-listbox.js"', rendered)
