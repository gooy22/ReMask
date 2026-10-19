# Pre-launch HTTP-only hardening

This change preserves the private cookie/proxy HTTP/2 transport, the existing
PRECHECK -> EXECUTE -> VERIFY -> COMMIT action engine, and retained no-replay
intents. It does not introduce Graph API tokens or a new advertising workflow.

## Runtime contract

- Neither production Dockerfile installs Chromium or Playwright. Legacy browser
  launch entrypoints reject before opening a renderer or sending a request.
- The embedded worker and standalone image select private-only provisioning.
  Fan Page preflight uses bootstrap and private Page reads; it does not require
  Business Suite UI. Incomplete private inventory preserves saved evidence and
  cannot establish an empty CREATE baseline.
- Development-only UI fixtures have a separate requirements file. Their browser
  is not a runtime dependency. Skipped legacy renderer fixtures are not counted
  as successful HTTP integration tests.
- Generic non-Meta HTTP helpers carry no Facebook cookies and cannot send Meta
  requests around the private HTTP/2 boundary.

## Defects addressed

1. AJAX state changes require POST. GET read actions have a closed policy; HEAD/OPTIONS cannot fall through into default saves;
   it also applies to internal-key callers. Session POST still requires CSRF.
   Worker retry cannot recover an arbitrary action from the query string.
2. Profiles, drafts, media/creative indexes, bundles and workspace metadata use
   stable lock files, complete writes, fsync and atomic replacement. Corrupt
   input fails closed. One previous valid snapshot is kept. Existing cache/job/
   usage temp writes also use the complete-write atomic helper.
3. JSON profile rows use authenticated CredentialVault encryption when the
   durable key is present. Production requires that key and migrates legacy
   profiles before serving. The previous snapshot is encrypted during migration
   too; wrong keys abort without overwriting the original. Historical external
   archives are not silently rewritten.
4. Queue scheduling reads durable SQLite work by available profile, with no
   unbounded list of item IDs in memory. Profiles rotate; waiting for a busy
   profile does not occupy a worker needed by another profile. Item claims use
   a conditional SQL update. Duplicate notifications cannot duplicate a claim.
5. A process lease covers initialization, recovery and execution. A second
   worker using the same database refuses startup. This release remains a
   single-replica embedded deployment; independent replicas with separate DBs
   are not a supported distributed scheduler.
6. The service refuses missing/default UI passwords and missing worker keys.
   URL passwords no longer authenticate. Password attempts are rate limited.
   Existing valid login sessions are preserved across deployment.
7. Worker logs go to stdout/stderr rather than an unbounded volume log. The PHP activity log rotates at 8 MiB with one previous segment. New
   media and jobs check capacity; media has a total quota. Readiness includes
   reserved free space. Live jobs/profiles/media are not deleted automatically.
8. Forty exact Python distributions are locked with SHA256 hashes. CI audits
   the lock and scans the built OS/library image for fixable HIGH/CRITICAL
   vulnerabilities, preserving the scan artifact. No claim about OS CVEs is
   made before that scan completes successfully.

## Operational settings

Existing REMASK_PASSWORD and REMASK_CREDENTIALS_KEY must be preserved. Do not
regenerate the credentials key on redeploy. REMASK_WORKER_API_KEY is mandatory
for a standalone worker; the embedded startup creates a private transient API
key and supplies it to the PHP bridge.

| Setting | Default | Meaning |
|---|---:|---|
| REMASK_STORAGE_RESERVE_BYTES | 33554432 | Free-space reserve, minimum 1 MiB |
| REMASK_MEDIA_LIBRARY_TOTAL_BYTES | 268435456 | Combined media files quota |
| REMASK_ACTIVITY_LOG_MAX_BYTES | 8388608 | Activity log segment size, one previous segment |
| REMASK_MAX_PENDING_ITEMS | 10000 | Pending/running items admission ceiling |
| REMASK_ENCRYPT_PROFILE_JSON | 1 in production | Require durable profile encryption |
| REMASK_HTTP_ONLY | 1 in production | Select private-only provisioning |

An 8 MiB serialized job limit bounds admitted configuration. Expanded bulk
items also count against pending admission and space checks. On low disk space,
new work is refused while reserve remains available to finish existing work.

## Verification

`tests.test_prelaunch_hardening` reproduces the prior GET mutation, interrupted
write, queue fairness, concurrent claim and wrong-key cases against the fixed
implementation. It also covers concurrent profile saves, corrupted-store
recovery, CSRF, media quota, missing/default credentials, generic cookie
isolation and browser launch rejection. All use disposable local state.

The complete installed PHP overlay test applies the new hardening overlay last,
after all historical overlays. Docker asserts Chromium and Playwright absence.
The full backend and JavaScript suites and the actual Docker/CI gates must pass
before deployment. Offline scheduler throughput is not Meta throughput; real
resource creation and audience availability still require an authenticated
live Meta response through ReMask.

A pre-existing invalid JSON file is never silently reset to an empty profile
list during startup. `AtomicJsonFile::restorePrevious(path)` is an explicit
operator recovery primitive; it does not auto-restore stale snapshots while
newer work may exist.
