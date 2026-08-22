from django.test import SimpleTestCase

from programs.utils.colors import get_contrast_color


class ColorContrastTest(SimpleTestCase):
    def test_contrast_color(self):
        # Light colors should return black
        self.assertEqual(get_contrast_color("#FFFFFF"), "black")  # White
        self.assertEqual(get_contrast_color("#FFFF00"), "black")  # Yellow
        self.assertEqual(get_contrast_color("#FFA500"), "black")  # Orange
        self.assertEqual(get_contrast_color("#00FF00"), "black")  # Lime

        # Dark colors should return white
        self.assertEqual(get_contrast_color("#000000"), "white")  # Black
        self.assertEqual(get_contrast_color("#000080"), "white")  # Navy
        self.assertEqual(get_contrast_color("#800000"), "white")  # Maroon
        self.assertEqual(get_contrast_color("#4B0082"), "white")  # Indigo

        # Mid colors
        self.assertEqual(
            get_contrast_color("#808080"), "white"
        )  # Gray (128,128,128) -> (128*299+128*587+128*114)/1000 = 128. Returns white since 128 is not > 128.

        # Invalid inputs
        self.assertEqual(get_contrast_color(None), "white")
        self.assertEqual(get_contrast_color(""), "white")
        self.assertEqual(get_contrast_color("red"), "white")
        self.assertEqual(get_contrast_color("#123"), "white")
