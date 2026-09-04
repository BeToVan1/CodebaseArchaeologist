# Archived, undeployed D1 design

Preserved for reference when moving admission storage to Oracle SQLite. These
files were never applied to the public Site according to the last read-only
preflight (no live database bindings or tables). They are not build inputs or
deployment migrations. Do not run this archived configuration. The current schema
is initialized explicitly by `deep_quota.py` on the existing Oracle disk.

Drizzle dependencies and their security override remain unchanged to avoid an
unrelated dependency/lockfile cleanup. No hosted database has been deleted.
