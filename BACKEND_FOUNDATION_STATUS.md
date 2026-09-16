# ReMask Backend Foundation

Development branch: `rebuild/backend-foundation`.

This branch is reserved for the clean-source rebuild derived from the last complete v7 source archive while production `ReMask-app` v11.2 remains untouched.

Foundation implemented and locally verified on 2026-09-16:

- single-flight Meta discovery cache with per-key locking;
- coalesced normal/forced/cold-forced refreshes to prevent duplicate Meta/proxy calls;
- reusable `MetaJobExecutor` for HTTP and CLI workers;
- browser/worker execution mode switch;
- background CLI worker compatible with current JSON Job storage;
- safe concurrent JSON JobItem claiming with flock locks;
- short-lived persisted Launch Review reuse;
- PostgreSQL PDO foundation and checksum-based migration runner;
- initial schema for profiles, ad accounts, assets, snapshots, bundles, media, jobs, job items, job steps and Meta usage;
- `JobStoreInterface` with runtime `json|postgres` backend selection;
- `PostgresJobStore` for create/get/process/retry/force-retry/delivery/history;
- PostgreSQL JobItem claiming uses `FOR UPDATE SKIP LOCKED` so multiple workers cannot claim the same item;
- authenticated profile credential encryption with XChaCha20-Poly1305 and AAD-bound profile/field context;
- PostgreSQL Profile store behind `REMASK_PROFILE_STORE=json|postgres`;
- PostgreSQL Bundle store behind `REMASK_BUNDLE_STORE=json|postgres`;
- dry-run-first legacy JSON -> PostgreSQL importer; writes require `--apply`, overwrites require an additional `--overwrite`;
- read-only migration verifier for profile names/credential round-trips, Bundle IDs/payloads and Job/JobItem state;
- storage health reports database status;
- Docker foundation includes `pdo_pgsql`;
- PHP syntax check across 80 files plus regression tests for credential encryption, cache coalescing, JSON Job concurrency and storage factories.

Railway infrastructure review confirmed that Volumes are service-bound. While media and legacy JSON state still live on `/data`, background workers must run inside the same `ReMask-app` container. Once Jobs and media references are fully moved to shared storage, workers can become independent Railway services.

The existing Railway service named `Postgres` is the intended database target for the clean-source preview; the second database `Postgres-PS_a` remains untouched. Production `ReMask-app` has not been connected or switched to PostgreSQL yet.

Current production is intentionally not switched to this branch yet. The next rebuild steps are: reconcile v8-v11.2 live features into clean source, validate migrations against a preview connected to the existing `Postgres` service, move media to shared object storage or another worker-readable backend, then move Railway deployment from env overlays to GitHub source.
