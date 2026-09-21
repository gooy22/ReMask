# ReMask async automation backend

This directory contains the asynchronous HTTP/session layer intended for a
separate ReMask worker/service.

## Components

- `AppSessionManager` — one isolated aiohttp session per profile with its own
  cookies, User-Agent, proxy, timeout and TCP connection pool.
- `BusinessLogicController` — generic command composition on top of the
  transport layer.

The module deliberately keeps target URLs and operation IDs outside the
transport class so ReMask can reuse the same worker for authorized QA,
integration and supported API workflows.

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r python_backend/requirements.txt
python python_backend/remask_async_automation.py
```

The `main()` block uses `qa.example.internal` placeholders and will not
perform real requests until replaced with an authorized environment.

## ReMask deployment

Recommended production topology:

```
ReMask PHP/API -> job queue/internal API -> Python aiohttp worker
                                      -> per-profile proxy session
```

Do not create one OS thread per Facebook/ReMask profile. aiohttp concurrency,
a global semaphore and independent per-profile TCP pools scale more cleanly.
