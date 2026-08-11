import colorsys
import inspect
import re
import unittest

from easymotor import theme


class ThemeTests(unittest.TestCase):
    def test_brand_palette_uses_logo_blue_colors(self):
        self.assertEqual(theme.PRIMARY_DARK, "#112947")
        self.assertEqual(theme.PRIMARY, "#284D76")
        self.assertEqual(theme.PRIMARY_MID, "#50769D")
        self.assertEqual(theme.PRIMARY_SOFT, "#8DA7C0")
        self.assertEqual(theme.PRIMARY_PALE, "#C8DBE7")

    def test_exported_theme_colors_are_valid_hex_rgb(self):
        colors = (
            value
            for name, value in vars(theme).items()
            if name.isupper() and isinstance(value, str) and value.startswith("#")
        )
        for color in colors:
            with self.subTest(color=color):
                self.assertRegex(color, re.compile(r"^#[0-9A-F]{6}$"))

    def test_theme_contains_no_saturated_red_colors(self):
        colors = (
            (name, value)
            for name, value in vars(theme).items()
            if name.isupper() and isinstance(value, str) and value.startswith("#")
        )
        for name, color in colors:
            red, green, blue = (
                int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)
            )
            hue, saturation, _value = colorsys.rgb_to_hsv(red, green, blue)
            hue_degrees = hue * 360
            with self.subTest(name=name, color=color):
                self.assertFalse(
                    saturation >= 0.35 and (hue_degrees <= 15 or hue_degrees >= 345),
                    f"{name} uses a saturated red color: {color}",
                )

    def test_notebook_selected_state_keeps_fixed_tab_geometry(self):
        source = inspect.getsource(theme.configure_theme)
        self.assertIn('padding=[("selected", (14, 7)), ("!selected", (14, 7))]', source)
        self.assertIn('borderwidth=[("selected", 1), ("!selected", 1)]', source)

    def test_notebook_tab_layout_omits_black_text_focus_outline(self):
        source = inspect.getsource(theme.configure_theme)
        layout_source = source.split('style.layout(\n        "TNotebook.Tab"', 1)[1].split(
            "style.map(", 1
        )[0]
        self.assertIn('"Notebook.label"', layout_source)
        self.assertNotIn('"Notebook.focus"', layout_source)


if __name__ == "__main__":
    unittest.main()
