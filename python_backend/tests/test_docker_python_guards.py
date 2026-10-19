"""Exercise the Docker image's Python source guards before a deployment build."""
import re
import shlex
import subprocess
import sys
import unittest
from pathlib import Path


class DockerPythonGuardTests(unittest.TestCase):
    def test_runtime_imports_without_any_playwright_package(self):
        script = '''
import builtins
original_import = builtins.__import__
def without_renderer(name, *args, **kwargs):
    if name == "playwright" or name.startswith("playwright."):
        raise ModuleNotFoundError("Playwright deliberately unavailable", name="playwright")
    return original_import(name, *args, **kwargs)
builtins.__import__ = without_renderer
import main, fb_worker
from app.payment_card_binding import profile_payment_card
from app.provisioning.registry import get_handler
from app.provisioning.private_create_handlers import business_handler, ad_account_handler
assert get_handler("BUSINESS") is business_handler
assert get_handler("AD_ACCOUNT") is ad_account_handler
assert callable(profile_payment_card)
'''
        result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_docker_python_source_guards_match_the_packaged_source(self):
        root = Path(__file__).resolve().parents[2]
        dockerfile = root / "Dockerfile"
        if not dockerfile.exists():
            # The image copies only python_backend. CI exercises the checkout.
            return
        command = re.compile(
            r"(?P<negative>!)?\s*grep\s+(?P<flags>-[A-Za-z]+)\s+"
            r"(?P<pattern>'[^']*'|\"[^\"]*\")\s+"
            r"(?P<path>/opt/remask-python/[^\s;]+)"
        )
        checks = list(command.finditer(dockerfile.read_text()))
        self.assertGreater(len(checks), 30, "Docker's Python source guards were not parsed")
        for check in checks:
            target = root / "python_backend" / check["path"].removeprefix("/opt/remask-python/")
            with self.subTest(target=target, pattern=check["pattern"]):
                args = ["grep", check["flags"], shlex.split(check["pattern"])[0], str(target)]
                result = subprocess.run(args, capture_output=True, text=True)
                self.assertEqual(result.returncode, 1 if check["negative"] else 0,
                                 f"Docker build guard failed: {args}; {result.stderr}")
