# ReMask checks through the deployed UI

Tests use the existing ReMask service and its authenticated interface. Direct
Meta browser actions are not used as proof that a ReMask feature works.

## Confirmed on deployment f15cc18

- Login session survives deployment and page reload.
- Workspace inventory loads 7 profiles, 4 Business Managers and 4 ad accounts.
- Searching profile 19 shows its two saved ad accounts and their correct parent
  Business Managers.
- Selecting RK 1 exposes its scoped action menu and Assets panel.
- The payment form receives its selected profile/account and the masked card list.
- The last saved card can be selected. F5 restores that card and the original
  payment batch; no card save or bind is replayed. No CVV was entered for this check.
- Launch loads the two saved profile-19 accounts, supports one-account selection,
  and explicitly reports saved/stale inventory as unverified.

## Defects corrected

Workspace failed module parsing because fetchAsset was declared twice. PR 134
replaces the original helper and checks the complete installed module in module
mode. Its deploy reached SUCCESS and Workspace loaded through the normal UI.

F5 preserved the payment batch but discarded the underlying Workspace row
selection and search. The scoped state helper saves only entity identifiers and
filters, prunes removed entities against fresh inventory, and does not use a
MutationObserver. Storage failures do not block normal UI operations.

## Explicit temporary direct testing

The worker's default remains assigned-profile proxy routing. The operator can
set REMASK_DIRECT_TEST_PROFILE plus REMASK_DIRECT_TEST_UNTIL (Unix seconds) for
one profile, for at most two hours. Its freshly resolved context uses a direct
connection while the saved proxy remains unchanged. Other profiles keep their
assigned proxies. Missing, expired, malformed and excessive deadlines grant no
exception. An existing direct HTTP client stops before new requests when the
deadline expires or changes. Newly resolved contexts return to saved routing.
Proxy checks identify DIRECT_TEST and proxy_verified=false; they do not claim a
healthy proxy. Worker health exposes the profile and expiry in the UI. Browser
fallback leases are also closed by the deadline. HTTP/2, cookie origin scope,
request deadlines and non-replay behavior remain in place.

## Still pending

Live session/inventory readback without proxy, scoped funding/card binding,
Assets reads, media library upload/edit/reuse, Bundle save/load, planning,
PAUSED publication, independent object readback, Jobs resume/reconciliation,
delivery controls and the remaining Workspace/Accounts actions require UI
execution after the direct test deployment. Neither card binding nor complete
end-to-end coverage is claimed by the local regression tests.
