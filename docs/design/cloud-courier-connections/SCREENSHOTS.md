# Screenshots

Captured at 2× from `Connections Capture Sheet.dc.html`. Dialogs are 640px wide (manager) and
600px wide (stepper) at 100% scale; heights are natural. The title bar is a stand-in — on Windows
it is system-drawn.

## Connections manager

| File | State |
|---|---|
| `01-light-manager.png` | Manager, light — four connections covering all three auth types |
| `02-dark-manager.png` | Manager, dark |
| `03-light-manager-in-use.png` | Delete refused, light — the in-use card expanded with its recovery route |
| `04-dark-manager-in-use.png` | Delete refused, dark |

## New connection stepper

| File | State |
|---|---|
| `05-light-step1-where.png` | Step 1 Where, light |
| `06-dark-step1-where.png` | Step 1 Where, dark |
| `07-light-step2-credential.png` | Step 2 Credential, light — both contractual strings in full |
| `08-dark-step2-credential.png` | Step 2 Credential, dark |
| `09-light-step2-health-gated.png` | Health gate, light — service unreachable, both paths disabled |
| `10-dark-step2-health-gated.png` | Health gate, dark |
| `11-light-step2-wrong-file-type.png` | Wrong file type, light — an OAuth client_secret rejected by name |
| `12-dark-step2-wrong-file-type.png` | Wrong file type, dark |
| `13-light-signin-in-progress.png` | Sign-in in progress, light |
| `14-dark-signin-in-progress.png` | Sign-in in progress, dark |
| `15-light-step3-validating.png` | Validating, light — the five preflight probes |
| `16-dark-step3-validating.png` | Validating, dark |
| `17-light-step3-success.png` | Verified, light — includes the delete-your-original-key notice |
| `18-dark-step3-success.png` | Verified, dark |
| `19-light-step3-failure.png` | Verification failed, light — a genuine 400 (no bucket access) |
| `20-dark-step3-failure.png` | Verification failed, dark |

## Transfer-flow comparison (optional deliverable)

| File | State |
|---|---|
| `21-transfer-option-a-one-screen.png` | Option A — the shipped one-screen flow |
| `22-transfer-option-b-wizard.png` | Option B — the same flow as three steps |

Only Option A is a real screen; Option B is an exploration. The written argument for each, and
the recommendation, are in `Transfer Flow Comparison.dc.html`.
