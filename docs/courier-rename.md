# Renamed: MML Cloud Transfer → MML Cloud Courier (2026-08-07)

Decided 2026-08-07, executed the same week: the product AND every
identifier renamed in one clean break — pre-1.0, no external users, no
compatibility aliases.

| | Old | New |
|---|---|---|
| pip name | mml-cloud-transfer | mml-cloud-courier |
| package | mml_cloud_transfer | mml_cloud_courier |
| console scripts | mmlct / mmlct-gui / mmlct-service | mmlcc / mmlcc-gui / mmlcc-service |
| env vars | MMLCT_* | MMLCC_* |
| Windows service | MMLCloudTransfer | MMLCloudCourier |
| data dir | %ProgramData%\MML Cloud Transfer | %ProgramData%\MML Cloud Courier |
| probe segment | .mmlct-preflight | .mmlcc-preflight |
| gate segment | mmlct-gate | mmlcc-gate |
| slice temp infix | .mmlct.tmp/ | .mmlcc.tmp/ |
| audit metadata key | mmlct-sha256 | mmlcc-sha256 |
| connectivity probe object | mmlct-connectivity-probe | mmlcc-connectivity-probe |
| emulator client project | "mmlct" | "mmlcc" |
| thread names | mmlct-worker / mmlct-api / mmlct-gui-* | mmlcc-worker / mmlcc-api / mmlcc-gui-* |
| DPAPI blob description | MML Cloud Transfer credential | MML Cloud Courier credential |
| service class | MmlctService | MmlccService |
| service display name | MML Cloud Transfer Service | MML Cloud Courier Service |

Historical records under `docs/superpowers/{specs,plans,gates}` keep the
old names on purpose — they document what actually happened. Objects
uploaded before the rename keep their `mmlct-sha256` metadata key; nothing
reads that key back (verification recomputes hashes locally).

The live migration completed 2026-08-07 (merge 62f1ff5): MMLCloudTransfer
was deregistered (already stopped, zero active jobs — all 15 terminal),
the data directory renamed in place (same volume; ACLs and machine-scope
DPAPI blobs unaffected), and the service reinstalled and started as
MMLCloudCourier (auto-start, restart-on-failure). One deviation from the
plan: a fresh install registers LocalSystem, so the log-on account was
restored to `.\pmaho` (the gate-sanctioned named-account config) via
services.msc afterward. Verified on the migrated install: /health ok,
all 3 profiles and jobs 1–15 intact, settings.json carried
`file_workers: 6`, suite green at 526 passed / 13 skipped on merged
master, and `mmlcc-gui` shows the full history. No user-scoped
MMLCT_OAUTH_CLIENT env var existed to migrate.
