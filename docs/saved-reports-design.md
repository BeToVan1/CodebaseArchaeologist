# Saved reports — proposed first persistence milestone

Status: offline design only. No bindings, migrations, authentication, provider
configuration, or billing settings have been changed. No deployment authorized
by this document. Product limits below are proposed, not current provider limits.

Offline implementation checkpoint: `worker/saved-report-policy.ts` now provides
strict metadata validation, pending-record construction, admission decisions and
owner/expiry read policy. Six tests cover forged capability fields, byte/count
boundaries, invalid metadata, and inaccessible states. These pure functions do
not authenticate callers, reserve storage atomically, sanitize report bodies,
persist bytes, or grant AI access. No route imports the module yet. The portable
report validator permits unknown fields; the separate saved-report serializer now
projects bounded JSON onto the supported browser report fields and revalidates it.
Unknown fields at nested object levels are removed; finite numeric metric names
remain data extensions. Raw analyzer-only fields not consumed by the browser are
not retained, so this is not a lossless archive of arbitrary analyzer output.
Source and explanatory text are preserved, not secret-scanned or authenticated.
HTTP streaming limits and payload digest computation remain integration work.

`worker/saved-report-lifecycle.ts` now models pending/ready/deleting/deleted
transitions with expected revisions and quota-release deltas. Five offline tests
cover metadata confirmation, missing writes, tombstone-before-release, duplicate
removal, foreign owners, expiry, and stale completion. These are pure transition
tests, not real database concurrency or storage integration tests. The adapter
must atomically compare-and-swap metadata and quota, and must fence/drain active
writers before confirming object removal. Otherwise a late upload could recreate
bytes after deletion. Storage confirmation events must never be client-supplied.
The current suite has 147 JavaScript tests; production build passes. No routes,
login flows, migrations, cleanup jobs, or storage bindings are enabled.

## Capability checkpoint — 2026-09-08

Read-only Sites inspection confirms this existing public Site is active, version
25, with an auth client configured. The installed Sites authentication guidance
supports dispatch-owned ChatGPT sign-in and per-Site stable authenticated user
IDs; anonymous visitors have no forwarded identity. This verifies an integration
path, not an end-to-end login test for our future My reports feature.

Use sign-in only for My reports. Browser sign-in must be a top-level navigation
to the dispatch-owned `/signin-with-chatgpt` route with a validated relative return
path, not fetch/prefetch. APIs reject missing server-established identity. Never
expose a directly reachable worker origin that trusts spoofable identity headers;
verify the dispatch trust boundary before activation. Do not equate ChatGPT login
with workspace membership or use email/network keys as ownership.

The retained legacy D1 files describe undeployed admission storage, not a report
library. Do not revive them or migrate Oracle quota data. Keep that archive intact;
saved-report schemas must be new and separately reviewed.

D1/R2 bindings remain null. Available connector tools do not expose storage-plan
entitlements or hard billing caps. No free-storage guarantee is established.
Before activation, owner/platform confirmation of entitlements and usage controls
is required under the project's no-unexpected-charges constraint. Application
admission limits alone cannot guarantee no provider charges. Until resolved,
continue only offline validation and mock-adapter work; no bindings, migrations,
storage provisioning, or public login rollout.

## Current implementation and constraints

- `.openai/hosting.json` has null D1 and R2 bindings. The public Worker does not
  implement account-owned saved reports.
- `app/graph-report.ts` validates portable reports, with a 10 MiB byte limit,
  10,000 nodes, 30,000 edges, and 500 files. Import/export is not durable storage.
- `evidence_store.py` holds trusted evidence in process memory, default TTL 900
  seconds, eight snapshots total and two per owner. Restart loses references.
- Hosted interpretation derives a network key in `worker/interpretation-route.ts`.
  This groups a network, not a person: shared networks and changed addresses make
  it unsuitable as ownership for a private report library.

## First slice

An authenticated user explicitly saves a completed report, sees it in My reports,
reopens it after reload or on another signed-in device, and deletes it. Anonymous
analysis and existing JSON import/export remain available. No automatic saving,
public sharing, organization membership, or model generation on save/open.

Use platform D1 for ownership/metadata and private R2 objects for serialized
reports, following Sites storage guidance. This is an architectural proposal,
not a claim these resources are configured or guaranteed free. Confirm hosting
entitlements, billing behavior, and hard usage controls before enabling them.
Do not move storage onto the small Oracle VM merely to avoid this decision.

Account identity is a prerequisite: use server-verified Sites-compatible login
and derive owner ID on the server. Never trust submitted owner IDs, IP/network
keys, guessed report IDs, or a client-supplied trust label. The login integration
must be verified against available platform capabilities before implementation.

## Trust model

Saved report access and AI evidence authorization are separate capabilities.

- First slice stores a strictly validated portable report submitted by its owner.
  Label it Saved report · unverified, including reports originally downloaded
  from our analyzer. Validation and a content hash do not prove source provenance.
- Reopening must not restore or mint `evidenceReference` or invoke the model.
  Strip session references/tokens and unrelated fields through allowlisted
  serialization. Treat source and explanatory strings as untrusted display data.
- Retained source is a copy from the saved report, not a fresh repository fetch.
  Show its reported commit, completeness and truncation. Reject unsafe source URLs
  under the existing link policy. Do not describe uploaded commit metadata as verified.
- Later trusted snapshots require server-to-server capture from a completed job,
  exact source bytes, pinned commit, and account-bound verification. Do not build
  that trust by promoting an uploaded report or reusing expired network references.
- No cross-owner deduplication or cache lookup in the first slice. Opaque random
  IDs identify reports, but every operation still checks authenticated ownership.

## Proposed data and API boundary

Metadata: opaque report ID, server owner ID, state, safe display title, reported
repository and commit, schema version, payload digest, byte count, created/expiry
timestamps, private object key, and trust origin fixed to uploaded-unverified.
Repository names and commit strings are display metadata, never object-key paths.

- `POST /api/reports`: authenticated same-origin bounded upload; validate and
  reserve capacity before persisting. Idempotency key is owner-scoped and bound
  to the payload digest; reuse with different content is a conflict.
- `GET /api/reports`: owner's bounded, paginated metadata only.
- `GET /api/reports/:id`: owner check, ready/unexpired check, bounded read,
  digest and schema validation, no-store response. Foreign/missing IDs share 404.
- `DELETE /api/reports/:id`: owner-checked idempotent deletion. No public R2 URL,
  bearer share URL, or unauthenticated list endpoint.

Use CSRF protection appropriate to cookie-based login, restrictive same-origin
checks, per-account admission limits and bounded streaming. Reject oversized
bodies before full buffering. Do not log source, payloads, tokens, or raw errors.

## Retention, deletion and budget proposal

Initial proposal: at most 10 reports and 50 MiB per account, 10 MiB per report,
30-day retention. Also require a configured global byte/count ceiling and write
rate limit before enablement. These are product admission limits, not billing
guarantees; read operations and cleanup may also incur provider usage.

Reject at capacity with a clear delete/export suggestion; no silent eviction.
Expiry prevents reads immediately, while cleanup removes bytes asynchronously.
Deletion first tombstones metadata so no further access is possible, then removes
the object and releases reserved quota only after confirmed removal. Do not promise
physical deletion while it is pending. Downloads already made cannot be revoked.
Backend backups/provider retention require a separate documented policy.

D1 and R2 do not share a transaction. Use a pending/ready/deleting state machine:
atomic owner/global reservations, unique object per report, bounded object write,
then mark ready. Failures remain charged until reconciled; readers never see
pending objects. Reconciliation must handle crashes between each step, partial
writes, orphan objects, stale reservations, and concurrent duplicate submissions.
Use an approved scheduled cleanup mechanism, not best-effort browser activity.

## Later explanation cache (not first slice)

Do not cache an interpretation merely by repository URL or symbol name. Cache
identity must include owner boundary, pinned repository/commit, analyzer build,
schema, node identity, exact evidence/source digests, prompt digest, provider/model,
generation settings, and validation-policy version. Explain that provider model
aliases can change; use a provider revision if available, otherwise bounded expiry.

Persist only validated results tied to trusted account-owned snapshots. A cache hit
is not fresh inference, independent verification, or proof of semantic correctness.
Never turn failures into successful cached explanations. Deleted/expired snapshots
invalidate derived results. Imported/unverified reports cannot populate trusted
AI caches. Cache misses must preserve explicit generation consent and quotas.

## Offline implementation order and acceptance

1. Implement strict saved-report envelope and pure quota/state-transition rules,
   tested with synthetic in-memory adapters. No storage bindings or login writes.
2. Verify and approve account login, D1/R2 availability, costs and hard controls.
3. Implement prepared-query metadata and private object adapters, append-only
   migrations, authenticated routes and My reports UI; validate locally.
4. Verify ownership isolation, same-network different users, changed IP, CSRF,
   malformed/oversized payloads, duplicate saves, concurrent capacity admission,
   failure recovery, expiry/deletion races, and absence of model/network fetches
   on reopen. Test exact source preservation and imported trust labels.
5. Obtain deployment approval after migrations, cleanup, retention policy and
   resource limits are reviewed. Keep existing live analysis and Oracle quotas intact.

Success means cross-session, account-owned reopening and deletion work within
limits without weakening evidence trust. It does not imply trusted persistent AI
evidence, public sharing, background analysis jobs, or explanation caching are done.
