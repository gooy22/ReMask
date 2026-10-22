# Private Launch DRAFT acceptance — 2026-10-22

## Current implementation state

PR [#245](https://github.com/gooy22/ReMask/pull/245) is merged to `main` at `d55851e37c7ae6ec3bcd6cf8e746ce1ce940e81a`.

The merged code adds per-target media preflight, replaces `adset.targeting` as a complete per-RK override, and preserves authentication/rate-limit failures as batch stops. The DRAFT path is separate from publication: it verifies the Business-owned Page and saved draft fragments, starts objects PAUSED, and returns without billing inspection, publication or delivery changes.

Main-commit CI completed successfully:

- Meta provisioning regression: run `39740246053` — backend regression, browser regression and payment guard jobs passed.
- ReMask Docker Diagnostic: run `39740246057` — passed.
- Build ReMask recovery image: run `39740246047` — passed.

Fixture coverage does not establish that Meta accepts a real draft. The live acceptance below is still outstanding.

## Single-target live acceptance

Run this through the existing ReMask Launch interface when the service and an authorized test profile are available. Keep the target count at one for this first acceptance. Do not use an active delivery status.

### 1. Readiness review

- Select one profile, Business, ad account and Page whose relationship is confirmed by fresh reads.
- Select **Черновик в Meta** and keep Campaign, Ad Set and Ad delivery PAUSED.
- Use one locally valid managed media item.
- Run **Проверить готовность в Meta**.
- Confirm the review returns `ready_for: DRAFT`, `payment_checked_live: false` and `publication_ready: false`.
- Confirm media is locally valid and the result does not claim Meta has accepted it.
- Confirm review creates no launch job and submits no advertising mutation.

### 2. Create and verify the draft

Submit once and record the job ID. Confirm the terminal result has:

- `draft_verified: true`
- `published: false`
- `verified: false` for final published objects
- exactly three fragment IDs for Campaign, Ad Set and Ad
- no `entities` and no `publication_id`

Confirm the saved draft and each fragment are read back independently. Check the account and draft identities, the Campaign → Ad Set → Ad parent relationships, exact selected settings and media binding, and Meta validation status. All created objects must remain PAUSED.

Confirm the job made no payment-method read, no `/publish` submission, and no delivery mutation.

### 3. Retry and restart recovery

- Repeat the same launch with the same idempotency key.
- If a worker restart is part of the acceptance window, repeat once after restart with that same key.
- Confirm the returned draft and fragment identities are unchanged and there are no additional draft, fragment or media-upload submissions.
- If a submission reply is unknown, use the saved operation's read/reconciliation path. Do not create a new key to bypass an unresolved submission.

### 4. Stop conditions

Stop the acceptance if scope, parent relationships, selected values, media identity or validation status cannot be independently confirmed; if Meta requires a checkpoint or returns a pending validation; or if any billing, publication or delivery operation appears. Preserve the saved job and operation key for read-only recovery.

Publication and payment acceptance are a later, separate milestone. They are outside this DRAFT acceptance.

## Evidence to record

Keep the acceptance result with the tested commit SHA, job ID, execution mode, readiness flags, terminal result fields, fragment verification outcomes, retry outcome and whether a restart was exercised. Do not put profile cookies, access material or payment data in the record.

## Status

**Code and GitHub CI:** complete.  
**Live Meta DRAFT acceptance:** pending; it was not run as part of this GitHub-only work.
