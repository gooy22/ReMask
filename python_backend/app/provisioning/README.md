# ReMask provisioning handlers

Runtime files in this directory:

- `business_handler.py` — validates BUSINESS input and returns `business_id`.
- `ad_account_handler.py` — requires saved `business_id` and returns `ad_account_id`.
- `funding_handler.py` — requires saved `ad_account_id` and returns `funding_source_id`.
- `registry.py` — maps provisioning step names to handlers.
- `transport.py` — shared route-based HTTP transport.
- `service.py` — state machine, SKIP/idempotency and transactional step persistence.

The external route configuration is read from `REMASK_PROVISIONING_ROUTES_JSON`.
