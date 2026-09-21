# ReMask Python worker

Separate asynchronous service for ReMask bulk jobs.

Implemented in v0.1:
- FastAPI job API
- persistent SQLite Job / JobItem / Task state
- restart recovery for QUEUED/RUNNING items
- per-profile locks
- configurable worker concurrency
- idempotent job creation
- Retry Failed
- internal profile resolver so cookies/proxy credentials are not persisted in the job database
- `proxy_check` task handler as the first end-to-end action

## API

- `GET /health`
- `POST /api/v1/jobs`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/retry-failed`

Example job:

```json
{
  "idempotency_key": "bulk-2026-09-22-001",
  "profiles": [
    {
      "profile_id": "profile-123",
      "tasks": [
        {"action": "proxy_check", "payload": {}}
      ]
    }
  ]
}
```

## Required environment

- `REMASK_WORKER_API_KEY` - optional API key required in `X-Remask-Worker-Key`
- `REMASK_PROFILE_RESOLVER_URL` - internal PHP endpoint returning `{cookies, proxy, user_agent}` for a `profile_id`
- `REMASK_INTERNAL_KEY` - optional key sent to the resolver in `X-Remask-Internal-Key`
- `REMASK_JOB_DB` - defaults to `/var/lib/remask-python/jobs.sqlite3`
- `REMASK_WORKER_CONCURRENCY` - defaults to `30`

Deploy this directory as its own Railway service using `python_backend/Dockerfile` and mount a persistent volume at `/var/lib/remask-python`.

## Current boundary

This first stage intentionally wires the queue, persistence, profile isolation, proxy validation, recovery and retry infrastructure before additional task handlers are registered. New handlers are added in `app/runner.py` without changing the Job API.


Deployment trigger: worker v0.2 e2e smoke.
