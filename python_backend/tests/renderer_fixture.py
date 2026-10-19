"""Optional renderer fixtures never add dependencies to the production image."""
import importlib.util
import shutil
import unittest

MISSING_PLAYWRIGHT = "Development renderer fixture unavailable (Playwright not installed)"


def renderer_executable(name):
    if importlib.util.find_spec("playwright") is None:
        raise unittest.SkipTest(MISSING_PLAYWRIGHT)
    return shutil.which(name)
