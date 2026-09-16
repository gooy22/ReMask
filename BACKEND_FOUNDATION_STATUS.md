# ReMask Backend Foundation

Development branch: `rebuild/backend-foundation`.

This branch is reserved for the clean-source rebuild derived from the last complete v7 source archive while production `ReMask-app` v11.2 remains untouched.

Foundation implemented and locally verified on 2026-09-16:

- single-flight Meta discovery cache with per-key locking;
- coalesced force refreshes to prevent duplicate Meta/proxy calls;
- reusable `MetaJobExecutor` for HTTP and CLI workers;
- browser/worker execution mode switch;
- background CLI worker compatible with current JSON Job storage;
- safe concurrent JobItem claiming with flock locks;
- short-lived persisted Launch Review reuse;
- PostgreSQL PDO foundation and migration runner;
- initial schema for profiles, ad accounts, assets, snapshots, bundles, media, jobs, job items, job steps and Meta usage;
- storage health reports database status;
- Docker foundation includes `pdo_pgsql`;
- concurrent cache and Job claim regression tests.

Current production is intentionally not switched to this branch yet. The next rebuild steps are: reconcile v8-v11.2 live features into clean source, migrate Job state to PostgreSQL, then move Railway deployment from env overlays to GitHub source.
