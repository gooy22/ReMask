"""Exercise the Docker image's Python source guards before a deployment build."""
import re
import shlex
import subprocess
import unittest
from pathlib import Path


class DockerPythonGuardTests(unittest.TestCase):
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
