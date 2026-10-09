# Business Page CREATE recovery

The latest production retry of Job `a7aa4f7649144be4999c06e5f5ffb1ca`
confirmed full operator access to RK `120251650486340295` in BM
`1451903470239662`, then received HTTP 200 for Page CREATE with no parsed Page
ID. Three exact-BM Page reads returned empty lists. The old trace did not retain
the response shape or error message; it cannot distinguish a rejection from
lost or unsupported response data. No successful second Page is claimed here.

This repair addresses three independently reproduced code defects:

- Relay terminal `data:null` frames erased an earlier result. Explicit deferred
  paths were merged at the root instead of at their target. The normalizer now
  preserves result IDs and errors, applies supported deferred paths, and rejects
  malformed, unsupported or explicitly unfinished streams.
- Page application rejections were treated as ambiguous creation forever.
  Bounded response evidence now records canonical Page/profile IDs, error
  messages/codes, field names and a digest. Only explicit name/user/integrity
  rejection with a returned null profile is classified as rejected; generic
  errors and missing/partial data retain duplicate protection.
- A created Page without a Business owner could not reach the existing CLAIM
  action. An independently identified unowned Page with confirmed claim
  permission now continues through Add an existing Facebook Page, exact-owner
  verification and full asset assignment. It is never reported as attached
  before those steps run.

Legacy retained submits without an ID also have a bounded positive-only
recovery path. Two HTTP documents of the current profile may expose managed
Page JSON. The document actor, canonical delegate Page ID, exact Page name,
fresh owner/claim permission and reservations must match. One eligible Page
can settle the desired bundle through CLAIM without another CREATE. Empty
documents, wrong actors/owners and ambiguous candidates never authorize POST.
This is allocation of a proven eligible Page, not a claim that the original
mutation response was recovered.

The ordinary pinned GraphQL contracts remain unchanged. Recovery uses no
Chromium, CSS selection or runtime doc_id discovery. Full RK rights are an
independent durable action. A second bundle is ready only after exact Page
ownership, Page/RK full operator rights and payment checks succeed.

Offline tests exercise both complete two-bundle flows and repeat Jobs without
another CREATE. Their success does not establish production Page creation.

## Follow-up: HTTP 400 during legacy recovery

Production Job `004ef22c684641ed9df3f81e17ce4101` at 06:02 UTC confirmed RK
rights, then returned three complete empty exact-BM Page inventories. The two
www profile documents returned HTTP/2 400. No new Page CREATE or CLAIM was
sent; the previous patch did not settle that live operation.

Recovery documents now explicitly use navigation headers, matching Business
document requests without changing generic API/bundle/CDN reads. This repairs
a request-construction discrepancy; the logs do not prove it caused the 400.
Two bounded Business Suite document reads also supply positive managed Page
candidates when www is unavailable. Every document must independently identify
the selected cookie actor, and every candidate still requires a fresh exact
Page name, owner and claim-permission read. Neither an empty document nor a
Business portfolio with no Pages authorizes replay of the retained CREATE.

Recovery records HTTP status, fixed host/path, bounded result classification
and a body digest in its durable checkpoint. It never stores HTML, cookies,
headers or CSRF values. These diagnostics survive successful action commit
too. Authentication rejection and rate limiting stop the remaining probes;
the old mutation intent and baseline remain retained.

The added two-bundle regression reproduces www 400 with a managed unowned Page
in a Business document, verifies Add existing Page and full asset assignment,
and runs the bundle again without any new mutation. Wrong actors, a Page owned
by another BM, empty documents and HTTP failures cannot bypass duplicate
protection. Live recovery remains unconfirmed until actual Meta evidence is
recorded; this test is not a successful production Page CREATE.
