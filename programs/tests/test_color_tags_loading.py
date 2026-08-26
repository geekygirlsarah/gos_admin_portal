from django.test import TestCase
from django.template import Template, Context

class ColorTagsLoadTest(TestCase):
    def test_load_color_tags(self):
        template_content = "{% load form_tags %}{{ '#FFFFFF'|contrast_color }}"
        template = Template(template_content)
        rendered = template.render(Context({}))
        self.assertEqual(rendered, "black")
