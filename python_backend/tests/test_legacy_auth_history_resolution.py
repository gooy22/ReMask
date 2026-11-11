import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.store import JobStore


class LegacyAuthHistoryResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = JobStore(str(Path(self.temp.name) / "jobs.sqlite3"))
        await self.store.init()
        self.cookies = {"c_user": "123456789", "xs": "current-session-secret"}
        self.store._record_profile_auth_cookie_revocation_sync(
            "32", "", "", [], int(time.time()), "legacy-job",
            "Legacy deletion history has no cookie snapshot.", "legacy_history_unverified",
        )

    async def asyncTearDown(self):
        self.temp.cleanup()

    def proof(self, **overrides):
        _, fingerprint = self.store._auth_cookie_fingerprint(self.cookies)
        proof = {
            "profile_id": "32",
            "document_kind": "saved_identity",
            "response_received": True,
            "identity_session_confirmed": True,
            "http_status": 200,
            "final_host": "www.facebook.com",
            "final_path": "/me",
            "final_identity_target_matches": True,
            "legacy_gate": "none",
            "login_form": False,
            "document_actor": "matches",
            "profile_actor_matches": True,
            "request_auth_matches": True,
            "saved_auth_snapshot_sent": True,
            "auth_cookie_directives_unchanged": True,
            "measurement_errors": 0,
            "auth_cookie_fingerprint": fingerprint,
            "wire": {
                "measurement_errors": 0,
                "requests": [{
                    "host": "www.facebook.com", "path": "/me", "method": "GET",
                    "user_agent_matches": True, "header_auth_state": "matches",
                    "header_auth_cookie_names": ["c_user", "xs"],
                }],
                "responses": [{
                    "host": "www.facebook.com", "path": "/me", "status": 200,
                    "protocol": "HTTP/2", "auth_cookie_deleted": [],
                    "profile_root_auth_cookie_deleted": [],
                    "auth_cookie_set": [], "profile_root_auth_cookie_set": [],
                }],
            },
        }
        proof.update(overrides)
        return proof

    async def test_exact_same_actor_identity_read_resolves_only_legacy_marker(self):
        resolved = await self.store.resolve_legacy_auth_cookie_history(
            "32", self.cookies, self.proof(),
        )

        self.assertTrue(resolved)
        self.assertFalse(await self.store.has_profile_auth_cookie_revocation("32"))
        self.assertIsNone(await self.store.profile_auth_cookie_revocation_for_context("32", self.cookies))
        with self.store._connect() as con:
            row = con.execute(
                "SELECT evidence_kind,actor_fingerprint,auth_fingerprint,message FROM profile_auth_cookie_revocations WHERE profile_id='32'"
            ).fetchone()
        self.assertEqual(row["evidence_kind"], "resolved")
        self.assertTrue(row["actor_fingerprint"])
        self.assertTrue(row["auth_fingerprint"])
        self.assertIn("GET /me", row["message"])
        self.assertNotIn("current-session-secret", str(dict(row)))

    async def test_unverified_or_cookie_deleting_probe_cannot_resolve_marker(self):
        for proof in (
            self.proof(identity_session_confirmed=False),
            self.proof(auth_cookie_directives_unchanged=False),
            self.proof(wire={"measurement_errors": 0, "requests": [], "responses": []}),
        ):
            with self.subTest(proof=proof.get("identity_session_confirmed")):
                self.assertFalse(await self.store.resolve_legacy_auth_cookie_history(
                    "32", self.cookies, proof,
                ))
                self.assertTrue(await self.store.has_profile_auth_cookie_revocation("32"))

    async def test_newer_legacy_history_wins_over_a_probe_of_an_older_marker(self):
        with patch.object(self.store, "_latest_explicit_auth_cookie_deletion_sync",
                          return_value={"source_job_id": "newer-job"}):
            self.assertFalse(await self.store.resolve_legacy_auth_cookie_history(
                "32", self.cookies, self.proof(),
            ))
        with self.store._connect() as con:
            row = con.execute(
                "SELECT evidence_kind FROM profile_auth_cookie_revocations WHERE profile_id='32'"
            ).fetchone()
        self.assertEqual(row["evidence_kind"], "legacy_history_unverified")


if __name__ == "__main__":
    unittest.main()
