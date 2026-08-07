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

The live service migration (reinstall as MMLCloudCourier, in-place data
directory rename — same volume, ACLs and machine-scope DPAPI blobs
unaffected) happens as a separate step after this branch merges; this
paragraph is updated to the completed record at that point.
