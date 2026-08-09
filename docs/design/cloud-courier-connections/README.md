# Handoff: MML Cloud Courier — connection management

## Overview

This package designs the one surface the previous round did not touch: **creating and managing
connections**. A connection is a named profile the Windows service stores — bucket, optional
default prefix, optional project ID, and a credential the service can use unattended.

Two deliverables:

1. **The connections manager**, rebuilt as status cards to match the shipped design language.
2. **The New connection stepper** — the current single dense dialog turned into a three-step
   guided flow, with every state designed: health-gated, wrong file type, sign-in in progress,
   validating, verified, and verification failed.

A third, optional piece — a side-by-side comparison of the transfer flow as one screen versus as
a wizard — is included as a judgment aid. It is not a proposal; the recommendation at the bottom
of that page is to keep the single screen.

## About the design files

The HTML files in this bundle are **design references**. They are not production code and must
not be ported, embedded, or wrapped in a web view.

The target is Python + PySide6 (Qt Widgets). Recreate these designs with `QDialog`, standard
layouts, and QSS driven by the `Theme` dataclass introduced in the previous round. Every
measurement is CSS pixels at 100% scale; Qt logical pixels map 1:1 at 100% DPI and Qt handles
scaling above that, so use the numbers directly.

Where this document and the codebase disagree, the codebase wins on *behavior* and this document
wins on *appearance* — with one exception, below, which is binding both ways.

## Fidelity

**High fidelity.** Colors, type, spacing and radii are final and come from the shipped token
table (`DESIGN_TOKENS.md` in the previous package; the same values are restated in the token
section here). Iconography stays programmatic per `icons.py`.

## The four contractual strings

`connection_dialogs.py` carries a module docstring stating that these phrases are
gate-findings-bound and asserted by tests. **They appear verbatim in this design.** Do not
reword them while implementing.

| Constant | Where it appears in this design |
|---|---|
| `COPY_CHOOSE_KEY` | Step 2, body text of the Service account key card |
| `COPY_CHOOSE_SIGNIN` | Step 2, body text of the Google sign-in card |
| `COPY_DELETE_ORIGINAL` | Step 3 success, in an `accent_soft` notice card |
| `COPY_SERVICE_FIRST` | Step 2 health gate banner |

Both credential strings sit at the moment of choice, in full, at 13px/1.5 — not behind a
tooltip, a `setWhatsThis`, or a "learn more". The 7-day expiry sentence and the least-privilege
recommendation are therefore unavoidable at the point the decision is made.

Two reworded alternatives are proposed in `RECOMMENDATIONS.md` (items 9 and 10) as ranked
suggestions requiring a test change. They are **not** applied here.

## Structure of the flow

```
Main window ─ Connections ─┐
                           ├─→ Connections manager (640 wide)
First run ─ Add a connection ─┘         │
                                        └─→ New connection stepper (600 wide)
                                              1 Where → 2 Credential → 3 Verify
```

The stepper's three steps map to the three decisions that genuinely must happen in order: you
cannot pick a credential before you know which bucket it must reach, and the service cannot
verify before it has one. That sequence is the reason this flow gets a stepper and the transfer
flow does not.

---

## Screen: Connections manager

**640 × natural height.** Title bar 34px, header block, a scrolling card list, a footer bar.

**Header** — padding `20 20 15`, `chrome` background, 1px `line` bottom.
- Title `Connections` at 17px/600, `letter-spacing: -.015em`.
- One sentence at 13px/1.5 `muted`: *"Each connection is a bucket and a credential the service
  keeps and uses on its own. Transfers pick one by name."* This is the only place the concept is
  ever explained, and scientists do not arrive knowing it.
- `New connection` as the single filled `accent` button, right-aligned, padding `9 15`, radius 6.
  It is the only action here that needs no selection, which is why it is the only filled control.

**Card list** — padding `15 20`, gap 11. One card per profile: `surface`, 1px `line`, radius 9,
padding `13 15`.
- Row 1: profile name at 14px/600, then an auth-type pill (10.5px/500 mono, uppercase,
  `letter-spacing: .05em`, padding `4 7`, radius 4).
- Row 2: `gs://bucket/prefix` at 11.5px mono `muted`, ellipsized.
- Row 3: the last-check line at 11.5px, 5px below.
- Right: `Check now` and `Remove` as outline buttons, padding `6 11`, radius 6, 11.5px/500.
  Per-card buttons — not a shared bar acting on a selection.

**Auth-type presentation** — the API returns raw enum values; the UI must not.

| `auth_type` | Pill label | Pill colors | Last-check line |
|---|---|---|---|
| `service_account_key` | `SERVICE ACCOUNT KEY` | `accent_soft` / `accent_text` | *"Checked 12 minutes ago — can list, read, write, compose and delete."* in `muted` |
| `oauth_user` | `GOOGLE SIGN-IN` | `warn_soft` / `warn_text` | *"Checked Aug 2 — this sign-in may have expired. Check it before the next transfer."* in `warn_text` when the last check is older than 7 days; the plain summary in `muted` otherwise |
| `adc` (legacy, CLI-created) | `COMMAND-LINE CREDENTIALS` | `track` / `muted` | *"Created outside this app. It works, but only this machine's signed-in account can use it."* |

`adc` is displayed, never offered. Its note explains the one thing that matters about it — that
it is machine-bound — without asking a scientist to know what Application Default Credentials
are. The amber `oauth_user` treatment is where the 7-day disclosure lands *after* creation; it is
the only recurring reminder the user will get.

**Footer** — padding `13 20`, `chrome`, 1px `line` top: a note at 11.5px `faint`
(*"Checking re-runs the same probe used when the connection was created."*) and `Close` as an
outline button.

### State: delete refused (profile in use)

`delete_profile` raises `ProfileInUse` → HTTP 409 with
`profile {id} is used by {n} job(s) and cannot be deleted while they exist`. Do not surface that
string. Expand the affected card with a `danger_soft` region, 1px `danger_edge` top border,
padding `13 15`:
- A 7px `danger` dot, then at 13px/600 `danger_text`: *"This connection is used by 7 jobs and
  cannot be deleted while they exist."*
- Beneath at 12.5px/1.5 `danger_text`: *"Their reports and bucket paths are read back through it.
  Delete or archive those jobs first, or leave this connection in place and stop using it for new
  transfers."*
- Two buttons: `Show those 7 jobs` (filled `danger`) and `Keep it` (outline `danger_edge`).

`Show those 7 jobs` closes the dialog and filters the main window's rail to that profile. That
turns a dead end into a route — it is the difference between an error and an answer.

The refusal appears **inline on the card**, not as a `QMessageBox`. A modal on top of a modal
loses the connection it is talking about.

---

## Screen: New connection stepper

**600 × natural height.** Title bar, header with step rail, body, footer.

**Header** — padding `18 20 15`, `chrome`, 1px `line` bottom.
- `New connection` at 17px/600, `letter-spacing: -.015em`, 14px above the rail.
- **Step rail**: three items, each a 20px circle plus a label, joined by 1px rules.
  - Completed: filled `accent` circle, `accent_ink` check glyph; label 12.5px/400 `muted`; the
    rule following it is `accent`.
  - Current: `accent_soft` fill, 1.5px `accent` border, `accent_text` numeral; label 12.5px/600
    `ink`.
  - Future: transparent fill, 1.5px `line` border, `faint` numeral; label 12.5px/400 `faint`;
    `line` rule.
- Labels: `Where` · `Credential` · `Verify`. One word each — the rail is a position indicator,
  not documentation.

**Footer** — padding `13 20`, `chrome`, 1px `line` top. `Back` outline at the left (disabled
styling on step 1), spacer, then `Cancel` outline and the step's primary button. At most one
filled button per footer.

| Step / state | Footer primary |
|---|---|
| 1 Where | `Next: credential` |
| 2 Credential | *(none — the two card buttons are the actions)* |
| Sign-in in progress | *(none — `Cancel` only)* |
| Validating | *(none)* |
| Success | `Done`, with `Back` relabelled `Add another` |
| Failure | `Try another credential` |

Step 2 deliberately has no footer primary. The choice between two credential paths is the whole
content of the step; adding a Next would imply a third, safer option that does not exist.

### Step 1 — Where

Section label `WHERE THE DATA GOES` (10.5px mono, uppercase, `letter-spacing: .08em`, `faint`),
then four fields with 15px gaps. Each field: a 12.5px/500 label with an `optional` marker at
11.5px `faint` beside it, the input (radius 6, 1px border, `surface`, 9–11px padding, 12.5px
mono value), then helper text at 11.5px/1.45 `faint`.

| Field | Helper |
|---|---|
| Name | *"How this appears when you start a transfer."* |
| Bucket | *"The bucket your administrator set up for this lab."* — with a static `gs://` prefix inside the field so nobody types the scheme |
| Default prefix *(optional)* | *"A folder inside the bucket that transfers start from. Leave it blank to start at the root."* |
| Project ID *(optional)* | *"Only needed when the credential does not name a project."* — placeholder reads `Taken from the key file`, matching `key_profile_payload`'s fallback to `key["project_id"]` |

The focused field carries an `accent_edge` border. Name and Bucket are required; `Next` stays
disabled until both are non-empty, replacing the current *"Name and bucket are required."* status
label, which today only appears after the user has already opened a file dialog.

### Step 2 — Credential

Section label `HOW THE SERVICE SIGNS IN`, then two cards, gap 11.

**Card A — Service account key.** `surface`, radius 9, padding `15 17`, **1px `accent_edge`
border** (the other card gets plain `line`) plus a `RECOMMENDED` pill in `accent_soft` /
`accent_text`. Body is `COPY_CHOOSE_KEY` verbatim at 13px/1.5. Action row: `Choose a key file…`
as the filled `accent` button, and beside it at 11.5px `faint`: *"A .json file your administrator
sends you."*

**Card B — Google sign-in.** Plain `line` border. Pill reads `CAN EXPIRE IN ~7 DAYS` in
`warn_soft` / `warn_text` — the disclosure as a glanceable label, above the sentence that states
it in full. Body is `COPY_CHOOSE_SIGNIN` verbatim. Action: `Sign in with Google…` as an outline
button, with *"Opens your browser."* beside it.

Below both, at 11.5px/1.5 `faint`: *"Either way, the service tests the credential against
gs://…/… before it saves anything."* The real bucket path from step 1 is interpolated.

The recommendation is expressed three ways — border weight, pill, and filled versus outline
button — so it survives being skimmed, while the sign-in path stays a normal, unpenalised choice.

#### State: health gate

`NewConnectionDialog` already runs `client.health()` on open and disables both credential paths
until it answers. Keep that logic exactly; give it a real presentation.

A `danger_soft` banner above the section label, 1px `danger_edge`, radius 9, padding `13 15`: a
7px `danger` dot, `COPY_SERVICE_FIRST` verbatim at 13px/1.5 `danger_text`, then two buttons —
`Check again` (filled `danger`) and `Open the main window` (outline `danger_edge`).

Both cards then go into a disabled treatment: headings to `faint`, body to `disabled`, pills to
`track`/`faint`, the key button to `track` fill with `disabled` text, the sign-in button to
`disabled` text. The cards stay readable — the user should still learn what the two options are
while the service comes up — but nothing looks pressable.

`Open the main window` is a new affordance and worth the line of code: the banner tells the user
to start the service from the main window, and this dialog may be covering it.

#### State: wrong file type

`load_key_file` raises with the actual type named. Show it inside Card A, in a `danger_soft`
sub-block, radius 6, padding `11 13`:
- The raw exception text at 11.5px/1.5 mono `danger_text`, `word-break: break-all` — the full
  path matters, since users routinely have several similar JSON files in Downloads.
- Then a plain-language line at 12.5px/1.5: *"That file is an OAuth client configuration, not a
  key. Use it under Google sign-in below, or ask your administrator for a service-account key."*

The key button relabels to `Choose a different file…`. Both credential paths stay enabled — the
most likely correct next action for a user holding a `client_secret_*.json` is the *other* card,
which this state points at explicitly.

### Sign-in in progress

Replaces the step-2 body while `run_login` is pending (5-minute timeout).
- A 44px ring, 3px `track` with an `accent` top segment, spinning.
- 16px/600 `ink`: *"Waiting for you to finish signing in"*.
- 13px/1.5 `muted`, max 400px: *"A browser window opened. Sign in there and allow access; this
  dialog carries on by itself."*
- 11.5px mono `faint`: *"gives up after 5 minutes"*.
- Below, a `surface` card with a `warn` dot: *"Nothing is saved yet. After sign-in the service
  still tests this credential against the bucket, and will refuse it if it cannot do everything a
  transfer needs."*

That last card exists because the browser hand-off is the moment users assume they are finished.

### Step 3 — Validating

Title 16px/600: *"Testing this credential against the bucket"*. Then 13px/1.5 `muted`: *"The
service writes a small object, composes it, reads it back and deletes it. An upload needs all
five; finding a gap now beats finding it overnight."*

Then a `surface` card listing the five probes from `PreflightResult`, one row each, padding
`11 15`, separated by `hairline`: a 16px status circle, the probe name at 12.5px/500, and a
detail string at 11.5px `faint`.

| Probe state | Circle |
|---|---|
| Passed | filled `accent`, `accent_ink` check |
| Running | transparent fill, `accent` border, empty |
| Pending | transparent fill, `line` border, label in `faint` |

Below the card, the target path at 11.5px mono `faint`.

This turns *"Validating the connection against the bucket…"* — one line that can sit unchanged
for fifteen seconds — into something that visibly advances. The five names also teach why the
permissions are needed, which pays off if the next screen is a failure.

### Step 3 — Verified

- A 24px filled `accent` circle with a check, then *"{name} is ready to use"* at 17px/600.
- A `surface` card headed `WHAT THE SERVICE FOUND` containing the service's own `summary()`
  string verbatim at 13px/1.5 `ink`, then the five capabilities as `accent_soft` chips.
- **The `COPY_DELETE_ORIGINAL` notice**: `accent_soft` card, 1px `accent_edge`, radius 9, a 7px
  `accent` dot, text verbatim at 13px/1.5 `accent_text`.
- Below, the original key file's full path at 11.5px mono `faint` — so "the original file" is a
  specific file the user can go and delete, not an abstraction.

The notice uses `accent`, not `warn` or `danger`. It is good news about key hygiene, not a
warning, and red stays reserved for failure.

### Step 3 — Verification failed

**The exact rejection contract matters here, so state it plainly.** `create_profile` rejects only
when `can_list` or `can_read` fails:

```python
result = preflight_fn(ctx, body.default_prefix)
if not (result.can_list and result.can_read):
    raise HTTPException(status_code=400, detail=result.summary())
```

So this screen represents a credential that cannot read the bucket at all — a wrong bucket name,
or a service account nobody granted access to. A credential that lists and reads but cannot
write is **not** rejected; it is saved, and the mixed summary comes back as the success payload.
That gap is real and is `RECOMMENDATIONS.md` item 1; do not paper over it here by pretending the
mixed case fails.

The screen:

- A 24px filled `danger` circle with `!`, then *"This credential cannot reach that bucket"* at
  17px/600 `ink`.
- A `surface` card with a 1px `danger_edge` border, headed `WHAT THE SERVICE FOUND`, carrying the
  service's own summary verbatim — here the no-capability branch of `PreflightResult.summary()`:
  *"This credential cannot access gs://mml-hi-imagery-2026/2026 at all."* Then five `danger_soft`
  capability chips, each with a `✕`.
  When the failure happens before the bucket is reached, `app.py` returns *"credential rejected
  before reaching the bucket: {classified message}"* instead; render that string in the same slot
  and drop the capability chips, since none were tested.
- A second `surface` card with the recovery path: *"The key is valid, but it has no access to this
  bucket. Either the bucket name is wrong, or nobody has granted this service account object
  access to it. Nothing was saved."* Then: *"Send your administrator the line above and ask for
  object access to this one bucket, nothing more."*
- Two outline buttons: `Copy this summary` and `Check the bucket name` (returns to step 1 with the
  Bucket field focused).
- Footer primary: `Try another credential` (returns to step 2 with the step-1 fields intact).

`Copy this summary` matters more than it looks. The recovery action is almost always an email to
an administrator, and the summary string is exactly what that email needs. Wrong bucket and wrong
permissions are the only two causes of this state, and each now has its own button.

---

## Behavior

No new endpoints. The API is `list` / `create` / `check` / `delete`, unchanged.

- **The health gate keeps its current semantics**: nothing credential-shaped is reachable until
  `/health` answers. The gate now covers step 2 as a whole rather than two buttons, so a user
  cannot open a file dialog by tabbing to a control that merely looks disabled.
- **Step 1 does not call the service.** Name and bucket are validated locally. The first network
  call is still `create_profile`, after a credential exists.
- **`create` validates before saving.** Steps 3's three states are the three outcomes of that one
  call: pending, 200, and 400.
- **`check` from the manager** reuses the same probe and rewrites the card's last-check line in
  place. No dialog.
- **Duplicate name** returns 409 (`a profile named 'x' already exists`). Surface it on step 1
  under the Name field, in `danger_text`, when the user returns from a failed create — not as a
  message box.
- **Theme** follows the same `Theme` object and `colorSchemeChanged` subscription as the main
  window. These dialogs must re-style live along with it.
- **Focus** order runs top to bottom, and every button carries the 2px `accent` focus ring at 2px
  offset defined in the previous package. The stepper must be completable from the keyboard.
- **Escape** cancels; on the sign-in step it also cancels the pending `run_login`.

## State

No new persisted state. Transient, held by the dialog:

- `step: 1 | 2 | 3`
- `fields: {name, bucket, prefix, project}` — survives a Back and a failed create
- `credential: dict | None`
- `phase: idle | signing-in | validating | verified | failed`
- `health_ok: bool`
- `last_error: str | None`

## Tokens

Identical to the previous package's `DESIGN_TOKENS.md` — same table, same rules. Nothing here
introduces a color. Reference values used in this design:

`accent` `#006ea0` / `#2eabe1` · `accent_text` `#005682` / `#67c4f2` ·
`accent_soft` `#e5f5fd` / `rgba(120,175,255,.10)` · `accent_edge` `#c8dfeb` /
`rgba(120,175,255,.20)` · `accent_ink` `#ffffff` / `#04111c` ·
`danger` `#c13c3b` / `#e8605b` · `danger_soft` `#ffe9e6` / `rgba(255,140,120,.12)` ·
`danger_edge` `#facfca` / `rgba(255,140,120,.22)` · `danger_text` `#892122` / `#ff9c8e` ·
`warn` `#b06f35` / `#e8a95c` · `warn_soft` `#feefdc` / `rgba(255,200,120,.12)` ·
`warn_text` `#774500` / `#eeba70` · `ink` `#12181f` / `#eaeff4` · `muted` `#4d5762` / `#a8afb5` ·
`faint` `#8a94a0` / `#7a8188` · `disabled` `#b9c2cb` / `#474e54` ·
`line` `rgba(18,24,31,.12)` / `rgba(160,195,235,.13)` ·
`hairline` `rgba(18,24,31,.06)` / `rgba(160,195,235,.07)` ·
`track` `#e3e9ec` / `#2d343a` · `surface` `#ffffff` / `#1a2128` · `bg` `#f4f6f8` / `#11171c` ·
`chrome` `#fafbfc` / `#0b1116` · `titlebar` `#eef1f4` / `#070d12`.

Type: Segoe UI Variable for interface, Cascadia Mono for paths, timestamps, capability chips,
and caps labels. Section labels 10.5px mono uppercase `+.08em`; body 13px/1.5; control labels
12.5px/500. Cards radius 9 with 1px `line` borders and no shadow; buttons radius 6; pills radius
4 for tags and 999 for status. 4px spacing base on an 11/13/15/20 rhythm.

## Assets

None. No images, no icon files. The spinner is a bordered circle with one edge in `accent`
(`QPropertyAnimation` on a rotation, or a small `QWidget` `paintEvent`); the checks and `✕` are
glyphs.

## Files in this bundle

| File | What it is |
|---|---|
| `README.md` | This document |
| `RECOMMENDATIONS.md` | Ranked UX changes beyond the visuals, with S/M/L costs, including the two copy-rewording proposals |
| `Connection Dialogs.dc.html` | Manager and stepper, every state, both themes. Props `dark`, `view` |
| `Connections Capture Sheet.dc.html` | All twenty states laid out for capture |
| `Transfer Flow Comparison.dc.html` | The optional one-screen vs wizard comparison, with the written argument for each |
| `screenshots/` | 2× PNGs of every state in both themes — see `screenshots/README.md` |

`Connection Dialogs.dc.html` is the authoritative reference. Open it in a browser and switch
`view` and `dark` to reach any state.
