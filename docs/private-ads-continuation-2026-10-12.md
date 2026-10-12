# Private advertising continuation

The post-28fdfb9 implementation described in the user's report was not found in
the available project directories, saved files, open PRs, or 208 repository
branches (205 distinct heads checked). No claim is made that it never existed
in another execution environment. Current production remains f8053f6.

The original source was recovered from the exact public URLs recorded in
28fdfb9. All 12 script hashes and all 38 selected full-module hashes match the
research snapshot. The recovered scripts are local research inputs; vendor
bundles and credentials are not added to the repository. The verification
metadata is in evidence/private-ads-source-recovery-20261012.json.

The new modules implement:

- the observed ApiClient scalar/JSON parameter encoding and draft value encoding;
- a profile-bound HTTP/2 private Ads Manager transport, initialized only from
  fresh scoped document configuration, with session credentials in memory;
- durable atomic submit claims and saved receipts on the existing Launch SQLite
  journal; an uncertain POST cannot replay, and a saved receipt resumes reads;
- Campaign, AdSet and Ad draft creation with independent draft/account/author,
  fragment/account/parent/value checks before publication;
- exact-operation publication polling and all-fragment/counter completion checks;
- four independent published-object reads for IDs, account, parents, PAUSED
  state, settings, and the creative's Page/media/content.

A terminal publication result is not independent object proof. The Creative ID
comes from the published Ad read and is then independently read itself.

The request/response recording and live publication remain unconfirmed. Fresh
ApiClient initializer layout is not established by the research snapshot: the
bootstrap accepts only literal supported ServerJS definitions/setter calls and
rejects absent or conflicting configuration. The captured graph origin/version
are defaults, not a captured SDK-routed network URL. Runtime integration, media
and all supported creative format adapters still need completion before this
prototype replaces the deployed Launch path. No production contract or route is
silently enabled by these added modules.

Validation: 30 new synthetic transport, journal, draft and published-object tests
passed. A combined 109-test run covering these tests and the existing Launch,
publication, delivery and real local HTTP/2 CONNECT transport tests passed.
No Meta advertising, payment or bank mutation was submitted by this work.
