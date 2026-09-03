# Hosted deep-analysis integration

Status (2026-09-03): owner reports the quota backend is installed, HTTPS analysis
passed, and the real ledger persisted across a service restart. Keep the old
image and `/etc/codebase-archaeologist/pre-quota-v1.service` for rollback.
The owner has now approved public website deployment and integration.
Deep activation remains gated on the actual Sites connecting-IP checks below.
See [oracle-quota-upgrade.md](oracle-quota-upgrade.md) for the pinned updater.

### Controlled website rollout

The first rollout keeps `ARCHAEOLOGIST_DEEP_ENABLED` false/unset. A temporary
`/api/network-probe` checks Sites forwarding without sending Oracle requests.
It is disabled by default (404); `ARCHAEOLOGIST_NETWORK_PROBE_UNTIL` must be an
ISO timestamp within the next hour and it automatically expires. It returns
only a domain-separated HMAC proof scoped to that expiry, presence, and whether
the documented shared Worker address was seen. No raw IP, token or admission
key is returned or stored; responses are no-store. The proof is a temporary
pseudonymous network identifier, not anonymous data or authenticated identity.
Compare repeated requests and spoofed forwarding headers from one network,
then a second independent network. Remove the diagnostic setting after checks.
Do not enable deep mode if spoofing changes the proof or distinct networks
collapse to the same identity. Presence alone does not establish header trust.

Owner-reported new-image validation: 113 tests passed, 1 skipped; itsdangerous
smoke produced 159 nodes/298 edges; isolated quota seed/persisted/missing checks
passed; the production upgrade then verified HTTPS analysis and persistence
across a real service restart. These supersede the pending backend steps in
the historical preparation notes below; full VM reboot was not tested.

## Request and trust boundaries

Browser -> same-origin `/api/analyze/deep` -> fixed Oracle HTTPS
`/api/analyze/quota-v1` -> persistent SQLite admission -> static analyzer.

The browser never receives the service token. The website requires same-origin
JSON, bounded input, the platform connecting-IP header, enable flag and secret.
It derives a domain-separated HMAC-SHA256 network key and forwards only that key,
the token and canonical repository URL. Browser-supplied keys, cookies, bearer
headers and X-Forwarded-For are ignored. Redirects and automatic retries are
disabled. Origin checks are not authentication; network limits are not per-person
limits or bot protection.

The versioned Oracle route is deliberately absent from the old runtime. An old
backend returns an error, never an unmetered job; the proxy never falls back to
the legacy endpoint. On the new runtime, both Oracle analysis routes enforce
the same ledger. Inventory remains the website's separate `/api/analyze` route.

Oracle authenticates, rejects an occupied slot, validates the bounded body/URL
and server-derived key, and reserves an admission before launching a job.
The database transaction enforces 3 admissions per network per rolling 600 seconds
and 30 globally per rolling 3600 seconds. The global limit holds even if keys
rotate. Failed/cancelled admitted analyses count; invalid inputs, unauthorized
requests and busy-slot rejections do not. Quota denial returns 429 with a fixed
quota marker and conservative 3600-second retry; busy returns 429/retry 5 seconds.
No storage error permits analysis.

## Persistent storage on the existing Oracle disk

`deep_quota.py` uses Python's built-in SQLite, with no added Python dependency.
The existing VM needs one dedicated directory, `/var/lib/archaeologist-quota`,
owned by 10001:10001 with mode 0700. Its ledger is `quota.sqlite3`, mode 0600.
The container gets only that directory as a writable bind mount; its root stays
read-only, non-root, with existing memory/CPU/PID/capability restrictions.

Initialize the schema explicitly using the new validated image and
`python -m deep_quota init /var/lib/archaeologist-quota/quota.sqlite3`.
Do not run this until the replacement image and deployment procedure are approved.
Initialization validates/reuses an existing ledger; it never clears it.
No initializer runs on service restart or in request handling. Requests use
SQLite `mode=rw`: a missing ledger/mount fails closed instead of resetting quotas.

Only keyed network identifiers, integer record IDs and timestamps are stored.
No raw IPs, tokens, repository URLs, code or reports. Expired rows are pruned at
the next admission transaction, including quota-denied requests; idle retention
can exceed one hour. At most 30 records remain after each transaction.
SQLite has a 1 MiB main-file page cap, DELETE journaling (no accumulating WAL),
FULL synchronization and a 150 ms lock timeout. Journals/filesystem overhead are
additional; this is not a filesystem quota or a complete disk-exhaustion defense.
The synchronous transaction is small and its lock wait bounded. Failures release
the request slot without starting work. Clock rollback can prolong a denial,
but cannot produce unbounded admissions.

The setup template now includes the mount and `ARCHAEOLOGIST_QUOTA_PATH`.
It still refuses to overwrite existing systemd configuration, so it is **not**
an in-place upgrade command. A reviewed replacement/rollback procedure is a
deployment gate. Never reset the ledger to make a test pass.

## Sites configuration and archived D1 work

`.openai/hosting.json` has `d1: null` and `r2: null`. No hosted database or
migration is required. The undeployed D1 schema/migration/config were moved
intact to `docs/legacy-d1/`, outside the build migration path. The unused
`db:generate` script was removed; dependencies/security override were preserved.
No production database or user data was deleted.

The prior Sites preflight found public access, owner role, version 14, and no
live database bindings/tables. The only runtime secret was the masked
`ARCHAEOLOGIST_SERVICE_TOKEN`; the enable flag was absent. This local work does
not change those hosted settings. Keep `ARCHAEOLOGIST_DEEP_ENABLED` false/unset.
The protected secret handoff was already completed; do not repeat it or paste
the secret in chat.

The owner could not locate account storage allowances. Moving the ledger avoids
adding a separate Sites database resource, but does not guarantee perpetual free
operation of Sites or Oracle. No upgrades or paid resources are authorized.

## Historical preparation validation and gates (before successful backend update)

Local validation of this change:

- 110 Python tests passed, 4 platform-dependent tests skipped on Windows.
- 42 JavaScript tests passed; TypeScript and production build passed.
- 52 setup/HTTPS-helper tests passed previously. The updated packaging suite has
  11 passing mocked scenarios; 7 additional mocked quota-container orchestration
  scenarios check network/mount restrictions, replacement sequencing and cleanup.
- Real SQLite tests cover concurrency, expiry, cross-process reopening, missing/
  corrupt/locked storage, rollback and file-size limits. HTTP tests verify both
  routes enforce quotas and rejected admissions do not spawn analysis.
- Container smoke source now initializes an ephemeral test ledger inside the
  disposable container and uses the versioned route. This is not evidence for
  persistence across real container replacement.
- Python dependencies were installed only into the ignored local test environment.
  No Docker build, SSH operation or deployment was performed for this migration.

Earlier **old-image** owner-reported Oracle results remain historical evidence:
90 tests passed/1 skipped; real itsdangerous analysis 159 nodes/298 edges;
runtime analysis 1.64 seconds, peak 104.53 MiB under 384 MiB, no observed OOM/restart;
incomplete-body disconnect recovered in 0.008 seconds; active-job observation
reaped one process, group empty, recovered in 0.219 seconds, no observed OOM/restart.
Normal completion can race cancellation. These do not validate the new image.

Remaining gates:

The updated Windows build/export command now includes the offline persistence
check automatically, before exporting a success manifest:

```powershell
& "C:\Users\bevan\Documents\Codex\2026-08-21\i-wa\work\hosted-analysis-trust\scripts\Test-DeepService.ps1" -MemoryMiB 384 -ExportOracleBundle
```

Run in the owner's normal Windows PowerShell with Docker Desktop running, not
on the Oracle VM. Agent Docker configuration access remains blocked; no bypass
or real Docker retry was attempted. Send the final PASS or first error.

The new check creates a uniquely labeled disposable Docker volume, sets only
that volume's permissions, seeds three synthetic admissions, and verifies quota
rejection from the actual HTTP service in a replacement container. Another
container with no mount must return 503. Both old and versioned routes are
checked. All four containers use network=none and no published ports; temporary
tokens are generated inside the container and never printed. No repository job
is expected or needed for these checks. The surrounding existing smoke tests
still submit the known small public repository. Cleanup removes only this run's
verified test volume and containers; no production volume is accessed.

A passing Docker Desktop test establishes persistence across replacement using
a Docker-managed test volume, **not** an Oracle reboot or the eventual host bind
mount. Those deployment-specific checks remain pending.

1. Build/test a replacement Linux amd64 image with the existing 384 MiB profile.
   Validate POSIX permissions, explicit initialization, persisted quotas across
   container restart/replacement, and disk/memory limits. Update the owner-run
   runtime verifier for the **new immutable image and mount**; its current pin
   deliberately still identifies the old approved runtime.
2. Obtain backend deployment approval; prepare reviewed backup/replacement and
   rollback steps that preserve the token, ledger, firewall and Caddy setup.
3. Verify the actual Sites connecting-IP contract. [Cloudflare's header
   documentation](https://developers.cloudflare.com/fundamentals/reference/http-headers/#cf-connecting-ip-in-worker-subrequests)
   describes intermediary rewriting/shared addresses. A nonempty header is not
   enough: test two real networks and spoof resistance or approve another
   identity design. SQLite relocation does not solve this uncertainty.
4. Obtain explicit public-publishing/activation approval. Every Sites URL is
   production, not an implicit private preview. Test the actual browser/proxy/
   Oracle path, quota feedback, cancellation and report validation under a
   controlled approved rollout.

Rollback of website functionality: disable the deep flag using an approved
deployment while retaining inventory/report import. Do not route to the old
unmetered Oracle endpoint. Preserve the ledger when changing backend images.
