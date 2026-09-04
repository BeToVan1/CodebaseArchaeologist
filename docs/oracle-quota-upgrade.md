# Approved Oracle backend update — execution pending

The owner approved the backend-only update, including a brief interruption.
Publishing/enabling the public Site is not approved. No SSH or real Docker
commands were executed by the agent; the owner runs this self-contained updater.

## Validation evidence

Owner-reported Oracle results for `oracle-6dc73a48a744469f8f28b581eea42ee7`:

- Archive SHA256: `6e06d52cd54c6070afd0989652837f63ccb21407bc4b2de44cb39e7bb41e9ec6`.
- Offline tests: 113 passed, 1 skipped, 2 warnings, 9 subtests passed, 4.10 seconds.
- Exact runtime: itsdangerous deep report, 159 nodes/298 edges, authorization and
  invalid-input checks passed.
- Oracle disposable-volume seed/persisted/missing checks all passed; no analysis
  jobs submitted and temporary volume removed. The production bind mount is
  still to be verified.

New image ID, verified from the archive's OCI index and observed on Oracle:
`sha256:b0fa42aa6579e01c8d823abe90d073328de9ffad5869bb0179c7361573478786`.
The initial updater incorrectly pinned the nested configuration digest
`sha256:1f4b0df20bd150c91ac00b67bd4d4cfc8042a3f67aa53a1fb5b51b4df3733468`,
which Oracle could not look up as an image. The failure occurred in preflight,
before unit backup, ledger initialization or service stop. The corrected pin was
verified through the archive index -> Linux amd64 manifest -> configuration chain,
rehashing every blob and the full archive. Exact-identity checks remain enabled.
Old image retained for rollback:
`sha256:320de275261a0a2e1255df7d971ee26322dbb3837e579521ff25eadc1f8efa07`.

## Owner-run procedure

In Windows PowerShell:

```powershell
scp -i "C:\Users\bevan\Downloads\Resume and Transcript\ssh-key-2026-09-02.key" "C:\Users\bevan\Documents\Codex\2026-08-21\i-wa\work\hosted-analysis-trust\scripts\upgrade_oracle_quota.py" ubuntu@159.54.182.161:upgrade_oracle_quota.py
```

Then in Ubuntu, while no other analysis or tests are running, run read-only
preflight and report its result:

```bash
sudo python3 ~/upgrade_oracle_quota.py
```

After preflight passes, apply the already-approved backend update:

```bash
sudo python3 ~/upgrade_oracle_quota.py --apply
```

The script repeats preflight and pins both images, verifies the exact old unit,
rejects backend overrides/pending systemd edits and checks runtime controls and
health/auth boundaries. It saves the old unit to
`/etc/codebase-archaeologist/pre-quota-v1.service`, refusing conflicting backups.
The private ledger is initialized/reused offline before downtime; never reset.

Only the analyzer unit changes, atomically: the new image ID, dedicated quota
bind mount and database-path setting. Existing token, Caddy, firewall, resource
limits and Sites settings are preserved. Verification includes loopback and
verified HTTPS, one small actual analysis, and persistence of its admission
across a service restart. Expect two brief backend interruptions, not a VM reboot.
The token is read only through its existing protected file, never printed,
regenerated, copied or placed in command arguments. The validation admission
remains in the real ledger. Keep the old image and unit backup for recovery.

## Failure and rollback

After service stop, errors trigger an attempt to restore/start/check the old
backend. Handled Python interruptions also take this path. Power loss/SIGKILL
cannot guarantee automatic rollback; the saved backup remains available.
Errors during initialization before downtime leave the old service running.

If instructed after failure/interruption:

```bash
sudo python3 ~/upgrade_oracle_quota.py --rollback
```

Rollback refuses unknown units/backups and preserves the ledger and both images.
Keep SSH open and report STOP, especially if rollback cannot be verified. Do not
rerun a successful apply; `--verify` checks the installed backend without another
analysis. Neither apply nor rollback enables public deep analysis.

Offline updater tests: 15 passed, including the available archive's hash chain.
They cover unit equivalence, ordering,
post-stop failures and interruptions, runtime/mount rejection, backup conflicts,
restart-ledger mismatch, failed rollback reporting and secret-safe errors.
Host commands are mocked; this is not evidence of real deployment execution.
