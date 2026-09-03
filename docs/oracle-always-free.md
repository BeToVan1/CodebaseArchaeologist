# Oracle Always Free deployment plan

## Current local change — not deployed

The owner approved moving usage-limit persistence to SQLite on this VM.
See [hosted-deep-integration.md](hosted-deep-integration.md) for implementation,
validation and release gates. The setup template now requires a **new quota-v1
image** and a dedicated persistent ledger directory. Do not run the old-image
setup command below with the updated script. It deliberately refuses to replace
existing configuration. The old runtime verifier is still pinned to the
previous immutable image; replacement-image/mount validation and deployment
approval are pending. All deployment commands and results below describe the
previous rollout unless explicitly stated otherwise.


Status: owner reports a provisioned E2.1.Micro VM in US West (San Jose), Ubuntu
24.04 x86_64, reserved public IP and successful SSH after host-key verification.
Docker client/server and a restricted hello-world container passed. Post-install
memory: 954 MiB total, 503 MiB available, no swap; disk: approximately 46 GB free.
A1 provisioning failed due to capacity. On Oracle, the transferred amd64 bundle
passed 90 tests (1 skipped) and the runtime GitHub smoke test: `pallets/itsdangerous`,
159 nodes, 298 edges, authorization verified, invalid URL rejected. These results
used the 384 MiB container profile. The owner subsequently started the persistent
analyzer and Caddy and reported verified HTTPS health, unauthorized rejection,
and authenticated invalid-input rejection. External Windows requests returned
200 health and 401 unauthorized using curl's per-command revocation best-effort
option; Windows revocation availability remains unresolved. Real analysis over
HTTPS passed: the owner reported `pallets/itsdangerous`
at commit `672971d66a2ef9f85151e53283113f33d642dabd`, deep tier, 159 nodes,
298 edges, in 1.84 seconds. Public-site integration is not deployed.
No public-site configuration changed.

The owner verified `codebase-archaeologist.duckdns.org` resolves to reserved IPv4
`159.54.182.161`, installed Caddy 2.6.2 and stopped it, and confirmed OCI ingress
allows TCP 80/443 from `0.0.0.0/0`. Keep port 8000 closed in OCI.

For this Micro VM, use the 384 MiB candidate profile and Windows-built amd64 bundle
in [deep-service-validation.md](deep-service-validation.md#oracle-micro-build-on-windows-not-on-the-1-gb-vm).
Repeat validation for any replacement image. Do not use the 1 GiB container allocation
below on this VM. The A1 sizing below is retained only as the original proposal.

## Micro VM: owner-run HTTPS setup

`scripts/configure_oracle.py` is a stdlib-only Ubuntu provisioning script. Its
12 offline unit tests pass; the owner reported successful systemd/Caddy setup and
certificate checks on Oracle. It does not install packages,
pull images, change DNS, or connect/publish the Sites website.

From Windows PowerShell, copy only this script (keep the SSH private key local):

```powershell
scp -i "C:\Users\bevan\Downloads\Resume and Transcript\ssh-key-2026-09-02.key" "C:\Users\bevan\Documents\Codex\2026-08-21\i-wa\work\hosted-analysis-trust\scripts\configure_oracle.py" ubuntu@159.54.182.161:
```

From the existing Ubuntu SSH session:

```bash
sudo python3 ~/configure_oracle.py \
  --hostname codebase-archaeologist.duckdns.org \
  --expected-ip 159.54.182.161 \
  --image codebase-archaeologist-deep:oracle-0948609e61814681a165a7958f9678ef
```

The script rejects unexpected DNS (including AAAA records), mismatched runtime
images, symlinks, conflicting files, other Caddy overrides, and an occupied service
port/container name on initial setup. It uses the loaded image's immutable ID.
It creates a random token in root-only `/etc/codebase-archaeologist/service.env`
(0600, parent 0700) and never prints it. Never paste this file or Docker environment
inspection output into chat. Identical existing configuration/token is reused;
different configuration is not overwritten.

Generated configuration:

- `codebase-archaeologist.service`: bounded, non-root, read-only Docker runtime;
  only `127.0.0.1:8000` is published, with rotating container logs.
- `archaeologist-web-firewall.service`: ensures just the IPv4 INPUT allow rule for
  TCP 80/443 at startup. No flushing, firewall snapshot restore, Docker-chain
  edits, InstanceServices changes, or SSH rule changes. If the host firewall is
  separately reloaded later, restart this oneshot unit to reapply the rule.
- `/etc/caddy/archaeologist.Caddyfile` and a dedicated systemd drop-in: HTTPS reverse
  proxy, preserving the default `/etc/caddy/Caddyfile` and Caddy certificate storage.

Analyzer memory is capped at 384 MiB (no additional swap); Caddy is capped at 64 MiB
with a 48 MiB soft limit. The Caddy limit is an unvalidated candidate for this
small VM. Check resource use before relying on it; do not remove limits to force
tests to pass. Successful tiny-repository tests do not establish capacity for
larger repositories or concurrent production use.

The initial readiness probe encountered a connection reset before Uvicorn finished
starting. A rerun succeeded. The local setup helper now retries connection resets
within its existing bounded readiness loop, with regression tests. This local fix
does not require restarting or reconfiguring the already-running services.

The script checks loopback health/auth before starting Caddy, then verified HTTPS
health, unauthenticated rejection (401), and authenticated invalid-input rejection
(400). HTTP redirects are not followed, and TLS verification is never disabled.
If the HTTPS authorization check fails, it stops Caddy. Other errors can leave
partially applied configuration/services; keep SSH open, report the STOP message,
and do not blindly replace files or remove resource limits. To stop exposure:

```bash
sudo systemctl stop caddy codebase-archaeologist
```

These services remain enabled after a manual stop and can start again on reboot.
For a longer pause, explicitly disable them as well. Subsequent checks still need
to cover VM memory use under larger workloads, reboot recovery,
image/dependency scanning, and the server-side Sites integration with abuse controls.

## Real repository over HTTPS: owner-reported PASS

Copy the standalone smoke helper from Windows PowerShell:

```powershell
scp -i "C:\Users\bevan\Downloads\Resume and Transcript\ssh-key-2026-09-02.key" "C:\Users\bevan\Documents\Codex\2026-08-21\i-wa\work\hosted-analysis-trust\scripts\test_oracle_https.py" ubuntu@159.54.182.161:
```

Then run on Ubuntu:

```bash
sudo python3 ~/test_oracle_https.py
```

This helper has eight offline unit tests (20 combined with provisioning). It
reads the existing protected token without printing it, uses only the approved
HTTPS hostname, rejects redirects, retains TLS verification, ignores ambient
HTTP proxies, and submits one `pallets/itsdangerous` job with no automatic retry.
The test has a 120-second total deadline and a bounded 10 MiB response read.
It checks schema/tier, repository identity, commit/pinned source URL, unique node
IDs and connected edge endpoints, and post-analysis health. Only a summary is
printed; no report or secret is saved or displayed. Node/edge counts may change
with the upstream repository revision. This is a smoke test, not a full schema,
semantic correctness, production-capacity, or public-site integration test.

The owner ran this helper successfully: `pallets/itsdangerous`, commit
`672971d66a2ef9f85151e53283113f33d642dabd`, deep tier, 159 nodes, 298 edges,
1.84 seconds. Repeat after changing the deployed runtime or proxy configuration.

Website integration work is described in [hosted-deep-integration.md](hosted-deep-integration.md).

## Micro runtime reliability check

The HTTPS helper accepts `--check-runtime`. Its 39 offline helper tests and
the 12 setup-helper tests pass locally. The full Linux service suite was not
rerun locally: the available Python runtime lacks pytest, and the prior Docker
permission restriction remains in place. Existing Oracle results above are
historical and distinct from the owner-run runtime check below.

The owner subsequently reported a live `--check-runtime` PASS: the same pinned
itsdangerous commit, 159 nodes, 298 edges, 1.64 seconds, 80.02 MiB before and
83.11 MiB after, with a 104.53 MiB container-lifetime peak under the 384 MiB cap.
No container replacement, restart-count change or OOM event was observed.

Copy the updated helper from regular Windows PowerShell, preserving the older
remote smoke helper under its existing name:

```powershell
scp -i "C:\Users\bevan\Downloads\Resume and Transcript\ssh-key-2026-09-02.key" "C:\Users\bevan\Documents\Codex\2026-08-21\i-wa\work\hosted-analysis-trust\scripts\test_oracle_https.py" ubuntu@159.54.182.161:test_oracle_runtime.py
```

Then run in the Ubuntu SSH session while no other analysis test is running:

```bash
sudo python3 ~/test_oracle_runtime.py --check-runtime
```

This reads selected Docker metadata and cgroup-v2 memory counters before and
after exactly one small `pallets/itsdangerous` HTTPS analysis. It verifies the
previously approved immutable runtime image, 384 MiB memory/no extra swap, one
CPU, 64 PIDs, read-only filesystem, non-root user, dropped capabilities, and
no-new-privileges. The test stops if these checks differ, an OOM event is present,
or the container identity/restart count changes. Missing counters fail the check
rather than weakening limits. No service configuration, image, firewall or file
on Oracle is modified by running it; no additional container starts.

Only selected resource readings and graph counts/commit are printed. The service
token stays in its protected file and is sent only to the fixed verified HTTPS
endpoint. Raw Docker environment, remote errors, reports and credentials are not
printed. The whole check retains the 120-second deadline and no analysis retries.

`container_lifetime_peak_mib` is the peak since that cgroup began, **not** the
peak attributable solely to this request. This is a small-repository observation,
not a load test, maximum-capacity guarantee, Caddy-memory measurement, or proof
of live cancellation/busy behavior. Those remain separate release checks.

## Busy rejection and incomplete-body disconnect

Owner-reported live PASS: busy HTTP 429, recovered validation HTTP 400 in 0.008
seconds, zero repository jobs, no restart/OOM event. Memory was 83.10 -> 83.60 MiB,
with the same 104.53 MiB lifetime peak. This verifies request-body disconnect
behavior only, not active-job cancellation.

Update the remote `test_oracle_runtime.py` helper using the same SCP command
above, then run this in the Ubuntu SSH session while no other tests or analyses
are running:

```bash
sudo python3 ~/test_oracle_runtime.py --check-request-lifecycle
```

This separate mode submits **zero repository-analysis jobs**. It checks health,
unauthorized rejection and idle validation, then opens one authenticated HTTPS
request declaring a 100-byte body but sends only `{`. A second invalid-input
request must receive 429 while health remains responsive. The held connection
is always closed, including on errors. At most six further invalid-input probes
check that the slot becomes available (400 for `{}`) before four seconds from
the original send, excluding the service's normal five-second body timeout.
Slow timing or proxy buffering yields INCONCLUSIVE rather than a false PASS.
The mode also checks runtime identity, isolation and memory before/after.

This occupies the single request slot briefly: do not run against other users'
analysis work. TLS verification remains enabled; the hostname is fixed, redirects
are not followed, and credentials/response bodies are not printed. Nothing is
installed or reconfigured. Run once and report the output; do not loop the test.

A PASS establishes busy rejection and **request-body** disconnect cleanup through
Caddy, not termination/reaping of an already-running analyzer process. Active-job
cleanup was subsequently observed below; Sites-to-Oracle cancellation remains unverified live. The
helper's 39 offline tests cover success, buffering/timing ambiguity, bounded
probes, connection cleanup and separation from real analysis mode.

## Active-analysis disconnect observation (owner-reported PASS, old image)

The owner reported PASS: active job observed, 429 busy, one observed process
reaped, group empty, 400 recovered in 0.219 seconds; same container, no observed
OOM, 83.10 -> 83.36 MiB, lifetime peak 104.53 MiB under the 384 MiB cap. Normal
completion can race cancellation. This does not validate the new SQLite image.


Copy the latest helper with the same SCP command above, then run once from the
Ubuntu SSH session while no other tests or analyses are running:

```bash
sudo python3 ~/test_oracle_runtime.py --check-active-cancellation
```

This submits exactly one request for the known small itsdangerous repository.
It observes only the validated container's cgroup process IDs and kernel
`/proc/PID/stat` metadata, not command lines, environments, or other host processes.
A newly isolated Python child with its own process group/session must be observed
running, and a second invalid request must receive 429 before disconnect. The
helper closes its own HTTPS connection and waits up to five seconds for the group
and observed process identities to disappear, including zombies awaiting reaping.
Process start times distinguish PID reuse. At most six invalid-input recovery
probes check slot availability; no analysis job is retried. Metadata field meanings
follow the [Linux kernel proc documentation](https://docs.kernel.org/filesystems/proc.html).

The helper never sends kill signals, restarts a service, changes files/configuration,
installs anything or disables TLS verification. A failure closes its connection
but does not manually intervene in server processes. Report STOP or INCONCLUSIVE;
do not select larger repositories or loop tests to force a result.

A PASS means a job was observed active immediately before disconnect, its observed
process group was reaped shortly afterward, and the slot recovered without a
container restart or OOM event. **Normal completion can still race cancellation**:
this is a live cleanup observation, not deterministic proof of the causal kill
path. Existing Linux lifecycle tests remain the deterministic coverage. Sites
end-to-end cancellation is a separate, still-pending check.

## Account and cost gate

The owner signs up at [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/)
and completes identity/payment verification privately. Do not share passwords,
card details, private SSH keys or service tokens in chat or Git.
Do not upgrade to Pay As You Go or select trial-only paid resources for this plan.
Confirm the home region with the owner before creating infrastructure.

Proposed initial VM (subject to console eligibility and existing account usage):

| Setting | Proposed value |
| --- | --- |
| Name | codebase-archaeologist-deep |
| Shape | VM.Standard.A1.Flex (ARM64) |
| Compute | 1 OCPU, 6 GB RAM |
| OS | Always Free-eligible Ubuntu ARM64 image |
| Boot disk | 50 GB, eligible default performance |
| Other services | No database, paid load balancer or extra disk |

As checked September 2, 2026, Oracle documents a total A1 allowance equivalent
to 2 OCPUs / 12 GB RAM and 200 GB combined boot/block storage. These are
account-wide allowances, not per-server entitlements. Check actual account limits
and all cost estimates before creating anything. If capacity is unavailable,
pause rather than substitute a paid shape. Idle instances may be reclaimed;
this is not a guaranteed-uptime hosting plan. Do not generate artificial load
to evade reclamation.

Sources: [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm),
[signup and trial rules](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm).

## Deployment gates

1. Obtain the owner's chosen region and approved SSH connection details. Keep the
   private key on the owner's machine; verify the host fingerprint independently.
2. Restrict SSH ingress to the administrator's IP. Do not expose port 8000 or the
   Docker daemon publicly. Maintain both OCI network rules and host firewall rules.
3. Transfer the reviewed source revision, not caches or credentials. Build and
   run the validation target natively on ARM64: the existing Windows Docker result
   does not establish ARM compatibility. Repeat offline tests and the GitHub smoke
   test described in [deep-service-validation.md](deep-service-validation.md).
4. Build the `runtime` target explicitly. The Dockerfile's final/default target is
   `validation`, which runs tests rather than serving the application. Pin and scan
   the chosen base image and dependencies before public rollout.
5. Preserve the tested container controls: one worker, non-root user, read-only
   filesystem, dropped capabilities, no-new-privileges, 1 GiB memory/no additional
   swap, 1 CPU, 64 PIDs and 128 MiB bounded /tmp. No host secrets, source mounts or
   Docker socket inside the analyzer. Configure bounded Docker log rotation.
6. Provision a cryptographically random service token through a protected runtime
   secret. Bind the API to loopback behind an HTTPS reverse proxy. Choose and verify
   a certificate-capable endpoint with the owner; a paid domain is not assumed.
7. Test authentication, request limits, timeout cleanup and outbound GitHub access.
   Keep repository parsing isolated; never install or execute repository code.
8. Only after backend validation, implement the Sites server-side proxy, per-user
   quotas and explicit deep-analysis selection. Store its token server-side only.
   Validate cancellation/timeout behavior and request approval before public release.

Keep the existing inventory and portable-report workflow available if the Oracle
service is unavailable. Record reproducible recovery steps before launch; the VM
must not become the sole copy of source code or reports.
