import unittest
from unittest.mock import patch

from tests.renderer_fixture import MISSING_PLAYWRIGHT, renderer_executable


class RendererFixtureTests(unittest.TestCase):
    def test_preinstalled_chrome_does_not_imply_playwright_is_available(self):
        with patch("tests.renderer_fixture.importlib.util.find_spec", return_value=None), \
             patch("tests.renderer_fixture.shutil.which", return_value="/usr/bin/google-chrome") as which:
            with self.assertRaisesRegex(unittest.SkipTest, "Playwright not installed") as error:
                renderer_executable("google-chrome")
            self.assertEqual(str(error.exception), MISSING_PLAYWRIGHT)
            which.assert_not_called()

    def test_dev_dependencies_keep_renderer_fixture_executable(self):
        with patch("tests.renderer_fixture.importlib.util.find_spec", return_value=object()), \
             patch("tests.renderer_fixture.shutil.which", return_value="/usr/bin/chromium"):
            self.assertEqual(renderer_executable("chromium"), "/usr/bin/chromium")
