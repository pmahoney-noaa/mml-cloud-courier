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

Historical records under `docs/superpowers/{specs,plans,gates}` keep the
old names on purpose — they document what actually happened. Objects
uploaded before the rename keep their `mmlct-sha256` metadata key; nothing
reads that key back (verification recomputes hashes locally).

The live service was reinstalled as MMLCloudCourier and the data
directory renamed in place (same volume, ACLs and machine-scope DPAPI
blobs unaffected); job history 1–15 carried over intact.
