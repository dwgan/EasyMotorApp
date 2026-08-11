import unittest

from easymotor.branding import APP_ICON_PATH, apply_window_icon


class _WindowStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def iconbitmap(self, bitmap=None, *, default=None) -> None:
        if default is not None:
            self.calls.append(("default", default))
        else:
            self.calls.append(("window", bitmap))


class BrandingTests(unittest.TestCase):
    def test_logo_exists_and_is_applied_to_window_and_taskbar_default(self):
        self.assertTrue(APP_ICON_PATH.is_file())
        window = _WindowStub()

        self.assertTrue(apply_window_icon(window, set_default=True))

        expected = str(APP_ICON_PATH)
        self.assertEqual(
            window.calls,
            [("window", expected), ("default", expected)],
        )


if __name__ == "__main__":
    unittest.main()
