# Profile 19 advertising continuation

The user identified profile 19 as having a verified card and authorized its use.
Workspace contains two confirmed BM/RK creation bindings for this profile. Their
saved funding display was not live proof of the card's current Meta status.

A fresh Workspace proxy check returned DEAD. A separate server-side HTTP-only
probe attempted the Facebook bootstrap through profile 19's configured proxy.
The connection raised RemoteRequestError before a usable response. The audit
reported browser_started=False and mutation_posts=0; Page, billing, upload and
publication queries were not reached. No card attachment, bank confirmation,
charge or advertising CREATE was submitted. The one-shot probe was disabled
afterward. A working profile proxy is needed for the live continuation.

## Changes

Workspace's asset panel still called the old token-backed metaAssetManager
route. Its list action now uses the reviewed private advertising READ bridge.
Pages, Pixel, audiences and media retain the selected account in every request
and cache key. The bridge and UI reject a response for another profile, account
or resource. Refresh waits for an older cached READ and then requests a fresh
READ. Missing mutation contracts are reported before any legacy Graph service.

The HTTP audit now reports typed proxy/connection/protocol/timeout failures,
the transport stage and whether submission was prevented. It does not log
exception text, connection URLs, session values or card credentials. An
authentication gate is reported only from its structured authentication evidence.

## Source review

Four archived Meta Relay queries were independently parsed at pinned archive
commit 0d88c7fddbde4cd33090a0b65df3d3017486684f in vinikjkkj/wa-diff:
AdsManagerLiveDataCampaignQuery and the three AdsMgmt dynamic Tier1 queries.
Their literal server selections return nodes, IDs and type names. The account,
delivery and campaign specification fields belong to client Relay resolvers.
Those fields cannot be treated as HTTP response proof of an advertising account,
PAUSED status, Page or parent relationships. No production operation was enabled
from these archived queries. The existing manifest remains unconfigured for
real advertising publication.
