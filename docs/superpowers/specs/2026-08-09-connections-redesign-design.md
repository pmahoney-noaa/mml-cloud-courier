# Connections redesign — design spec

Date: 2026-08-09
Status: approved (brainstorm decisions taken by Peter Mahoney in session)
Design source: `docs/design/cloud-courier-connections/` (README.md is authoritative;
RECOMMENDATIONS.md is ranked; SCREENSHOTS.md indexes the untracked PNG captures in
`.claude/design/design_handoff_cloud_courier_connections/screenshots/`).

Where this spec and the handoff README disagree, this spec wins — every divergence is an
explicit, user-approved gap-fill listed in §9. Otherwise: the handoff wins on appearance,
the codebase wins on behavior.

## 1. Decisions taken during brainstorming

| # | Question | Decision |
| --- | --- | --- |
| 1 | Transfer flow A/B (screenshots 21 vs 22) | **Keep one screen**, graft Option B's per-step explanations as helper text under Prefix and Job name (§7) |
| 2 | RECOMMENDATIONS item 1 (read-only credential saves silently) | **Option 3 only**: the verified screen shows the service's own mixed summary verbatim. Options 1/2 deferred — they change the fixed profiles API |
| 3 | Copy rewordings (items 9, 10) | **Keep all four contractual strings verbatim.** No pinned-test edits. The `CAN EXPIRE IN ~7 DAYS` pill carries item 9's intent; the mono key-path line carries item 10's |
| 4 | Legacy `adc` profiles | **Display-only** in the manager (pill + machine-bound note; Check/Remove work normally); the stepper never offers adc |
| 5 | Remaining triage (items 2–8, 11, 12) | **All in**, including the rail profile-filter behind `Show those N jobs` |
| 6 | Code structure | **Thin public module + flat helpers**: `connection_dialogs.py` keeps its public surface; new flat `gui/connection_widgets.py` holds shared primitives (stays inside the hex-test scan) |

## 2. Binding constraints (violations are defects)

- The four contractual strings in `connection_dialogs.py` — `COPY_CHOOSE_KEY`,
  `COPY_CHOOSE_SIGNIN`, `COPY_DELETE_ORIGINAL`, `COPY_SERVICE_FIRST` — remain **verbatim**.
  The pinned tests in `tests/gui/test_connection_dialogs.py` are not modified.
- Profiles API is **fixed** — no new endpoints, no payload changes. GUI uses exactly:
  `list_profiles`, `create_profile` (server validates against the bucket before saving),
  `check_profile`, `delete_profile` (refuses in-use with 409). GUI creates only
  `service_account_key` and `oauth_user`.
- The service-health gate stays: nothing credential-shaped (file browse, browser sign-in)
  is reachable until `/health` answers.
- The 7-day OAuth 'testing' expiry honesty and the least-privilege key recommendation stay
  prominent at the credential-choice step (both strings render in full at 13px/1.5 on the
  choice cards — never behind a tooltip or `setWhatsThis`).
- All colors come from `gui/theme.py` `Theme` tokens; `test_no_hex_colors_outside_theme_py`
  stays green. Red = failure only (success and the delete-original notice use `accent`).
- New GUI modules sit **flat in `gui/`** so the hex test's non-recursive `gui/*.py` glob
  covers them.

## 3. Module structure

- **`gui/connection_dialogs.py`** (public surface unchanged): the four constants,
  `load_key_file`, `key_profile_payload`, `oauth_profile_payload`, `ConnectionsDialog`
  (manager), `NewConnectionDialog` (stepper). Existing import paths and the six existing
  tests keep working.
- **`gui/connection_widgets.py`** (new, flat): shared visual primitives — card frame, pill,
  status dot, step rail, probe list, inline notice, ring spinner, section label. Each is a
  small QWidget styled from `Theme` tokens, `WA_StyledBackground` set, restyled live via
  `theme.notifier.changed` (bound-method auto-disconnect flavor, as in `job_tabs.py`).
- **`gui/format.py`** gains `split_service_error(message) -> tuple[int | None, str]`:
  `call_async` delivers failures as `str(exc)` = `"409: profile 4 is used by 7 job(s)…"`;
  the manager (delete refusal), the stepper (duplicate name, preflight 400) and generic
  error display need the status code and detail separated. Also a new relative-time
  formatter for "Checked 12 minutes ago" / "Checked Aug 2" — `format.py` has
  `human_schedule` for ISO timestamps but nothing relative today.

## 4. Connections manager (`ConnectionsDialog`, 640 × natural)

Per the handoff README "Screen: Connections manager" — title bar, header, scrolling card
list, footer. Specifics and behavior:

- **Header**: title `Connections` (17px/600); the definition sentence ("Each connection is
  a bucket and a credential the service keeps and uses on its own. Transfers pick one by
  name."); `New connection` as the only filled (`accent`) button.
- **Card list**: one card per profile from `list_profiles` (`surface`, 1px `line`, radius 9).
  Row 1 name + auth pill; row 2 `gs://bucket/prefix` mono ellipsized; row 3 last-check
  line; right side per-card `Check now` and `Remove` outline buttons. No selection-driven
  shared button bar; footer holds only the probe note and `Close`.
- **Auth pills**: `service_account_key` → `SERVICE ACCOUNT KEY` (`accent_soft`/`accent_text`);
  `oauth_user` → `GOOGLE SIGN-IN` (`warn_soft`/`warn_text`); `adc` → `COMMAND-LINE
  CREDENTIALS` (`track`/`muted`) with the note "Created outside this app. It works, but only
  this machine's signed-in account can use it." Raw enum values never appear.
- **Last-check line**: "Checked {relative}" from `validated_at` ("never" handled). For
  `oauth_user` older than 7 days: amber (`warn_text`) — "Checked Aug 2 — this sign-in may
  have expired. Check it before the next transfer."
- **Check now**: replaces the card's last-check line with "Checking…", disables that card's
  buttons, calls `check_profile(id)`, then rewrites the line in place with the response's
  real `summary` (and refreshed relative time). No dialog. Failure renders the detail on
  the line in `danger_text`.
- **Remove**: expands the card inline with a `danger_soft` confirm region — "Remove
  '{name}'? Its saved credential is deleted with it. This cannot be undone." with `Remove`
  (filled `danger`) and `Keep it` (outline). On confirm, `delete_profile(id)`.
- **Delete refused (409 `ProfileInUse`)**: the confirm region is replaced in place by the
  refusal region per the README — `danger` dot, "This connection is used by {n} jobs and
  cannot be deleted while they exist." (n parsed from the detail string), the two-sentence
  explanation, and `Show those {n} jobs` (filled `danger`) + `Keep it`. The raw
  "profile {id}…" string is never shown. `Show those N jobs` emits a signal and closes the
  dialog (§6).
- **Empty state**: a single muted line, "No connections yet." (the first-run screen covers
  the real cold start).
- **List failure**: the error detail replaces the list content as a `danger_text` line.

## 5. New connection stepper (`NewConnectionDialog`, 600 × natural)

One `QDialog`: custom header (title + three-item step rail `Where · Credential · Verify`),
`QStackedWidget` body, custom footer (`Back` left; `Cancel` + at most one filled primary
right). Not `QWizard`. Transient state exactly as the README State section: `step`,
`fields` (survive Back and a failed create), `credential`, `phase`
(`idle | signing-in | validating | verified | failed`), `health_ok`, `last_error`.

Footer per state follows the README table (step 2 and the pending states have **no**
footer primary).

- **Step 1 — Where**: Name, Bucket (static `gs://` prefix rendered inside the field frame),
  Default prefix (optional), Project ID (optional, placeholder "Taken from the key file");
  helper text under each field per the README, always as labels, never placeholders
  (except the Project ID fallback note, which the README specifies as a placeholder).
  `Next: credential` disabled until Name and Bucket are non-empty — replacing the
  after-the-fact "Name and bucket are required." status flow. Step 1 makes no network call.
- **Step 2 — Credential**: Card A (service-account key: `accent_edge` border, `RECOMMENDED`
  pill, `COPY_CHOOSE_KEY` verbatim, filled `Choose a key file…`, ".json file" side note);
  Card B (Google sign-in: `CAN EXPIRE IN ~7 DAYS` pill in `warn_soft`/`warn_text`,
  `COPY_CHOOSE_SIGNIN` verbatim, outline `Sign in with Google…`, "Opens your browser.").
  Below both: "Either way, the service tests the credential against gs://…/… before it
  saves anything." with the real step-1 path interpolated. OAuth client config source stays
  as today: `MMLCC_OAUTH_CLIENT` env var, else a file-browse for the client JSON.
- **Health gate**: `client.health()` on open, kept; while unanswered/down, the
  `danger_soft` banner (dot + `COPY_SERVICE_FIRST` verbatim + `Check again` filled danger +
  `Open the main window` outline) sits above the section label and both cards take the
  readable-but-disabled treatment — headings `faint`, body `disabled`, pills
  `track`/`faint`, nothing pressable, no tab-reachable credential action. `Check again`
  re-runs `health()`. `Open the main window` closes the modal dialog chain (stepper, and
  the manager if it is beneath) and then raises/activates the main window — the handoff's
  "without closing the stepper" is unimplementable over exec()-modal dialogs on Windows
  (ruled in review).
- **Wrong file type**: `load_key_file`'s exception text renders inside Card A in a
  `danger_soft` sub-block — raw text in mono (`break-all`; the path matters), then "That
  file is an OAuth client configuration, not a key. Use it under Google sign-in below, or
  ask your administrator for a service-account key." The key button relabels to `Choose a
  different file…`; both paths stay enabled.
- **Sign-in in progress**: replaces the step-2 body — spinning ring (44px, `track` +
  `accent` segment), "Waiting for you to finish signing in", the browser-carries-on line,
  "gives up after 5 minutes" in mono, and the nothing-saved-yet `surface` card with a
  `warn` dot. Footer: `Cancel` only. Escape/Cancel abandons the pending `run_login`
  (see §8, cancellation).
- **Step 3 — Validating**: title "Testing this credential against the bucket", the
  writes/composes/reads/deletes explainer, then the five-probe card (list, read, write,
  compose, delete) with passed/running/pending circles, target path in mono below.
  **Client-side pacing**: no per-probe service signal exists and none is added. A timer
  advances the rows while the single `create_profile` call is pending; the last probe
  stays "running" until the response lands. On 400 the timer stops and the failed screen
  takes over — individual probes are never marked failed, since which one failed is
  unknown. Cancel stays enabled during validating; a cancelled dialog discards late
  create results (generation guard), and both call sites re-fetch profiles when the
  stepper closes, so a create that completed server-side still surfaces (ruled in
  review).
- **Step 3 — Verified**: `accent` check circle, "{name} is ready to use", `WHAT THE
  SERVICE FOUND` card with the response's `summary` **verbatim** (a mixed summary — e.g.
  read-only — appears as-is; that is decision 2), five `accent_soft` capability chips,
  then **for `service_account_key` creations only**: the `COPY_DELETE_ORIGINAL` notice
  (`accent_soft` card, `accent_edge`, `accent` dot) with the chosen key file's full path
  in mono beneath. OAuth creations show summary + chips only — there is no original file.
  Footer: `Done` (closes, emits `created` with the create response); `Back` relabelled
  `Add another` (resets to a pristine step 1). Both call sites (`ConnectionsDialog`,
  `NewTransferWizard`) keep their existing `created`-signal refresh behavior.
- **Step 3 — Failed** (`create_profile` 400): `danger` circle with `!`, "This credential
  cannot reach that bucket", `WHAT THE SERVICE FOUND` card (1px `danger_edge`) carrying
  the service's detail verbatim. Two variants: the preflight summary → five `danger_soft`
  ✕ chips; a detail starting "credential rejected before reaching the bucket" → same slot,
  **no chips**. Recovery card with the wrong-bucket-or-no-grant explanation and
  "Nothing was saved." Buttons with the message: `Copy this summary` (clipboard) and
  `Check the bucket name` (→ step 1, Bucket focused). Footer primary: `Try another
  credential` (→ step 2, fields intact).
- **Duplicate name** (create 409): return to step 1 with the detail under the Name field
  in `danger_text`. Never a message box.
- **Other failures** (network mid-create, etc.): the failed screen with the message in the
  summary slot, no chips.

## 6. Rail profile filter ("Show those N jobs")

- `ConnectionsDialog` gains `showJobsForProfile = Signal(int, str)` (profile id, name).
  `MainWindow._open_connections` connects it; on emit the dialog closes and the main
  window sets a profile filter.
- Filtering is client-side: jobs from `GET /jobs` already carry `profile_id`. While a
  filter is active, `_on_jobs` (and the initial application) sync the rail from the
  filtered list; a slim bar above the rail reads "Showing jobs using {name}" with a
  `Show all` action that clears the filter and restores the full rail.
- The first-run gate (`_update_first_run`) keeps consulting the **unfiltered** job list.
  If the selected job is filtered out, selection clears. Polls preserve the filter until
  cleared.

## 7. Transfer dialog helper text (A/B outcome)

`NewTransferWizard` (stays one screen) gains two faint wrapped helper labels, strings
lifted verbatim from the Option B exploration:

- Under **Prefix**: "A connection is a bucket and the credential the service uses. The
  prefix is the folder inside it."
- Under **Job name**: "Anything already in the bucket and unchanged is skipped, so
  nothing is sent twice."

No other transfer-flow changes.

## 8. Cross-cutting behavior

- **Theme**: both dialogs and all primitives restyle live on `theme.notifier.changed`;
  QSS fragments are built from `Theme` fields only. Type ramp, radii, spacing per the
  handoff token section (Segoe UI Variable / Cascadia Mono via `mono_font`).
- **Buttons**: at most one filled button per region; footer primary gets `setDefault`,
  everything else `setAutoDefault(False)` (Qt gotcha: `autoDefault` makes the first button
  the Enter target). Focus ring comes from the app QSS (2px `accent`, 2px offset).
- **Keyboard**: full flow completable from the keyboard; focus order top to bottom.
  Escape cancels the dialog; during sign-in it first abandons the pending login.
- **Cancellation**: the plan must check `auth/oauth_flow.py` for a real cancel hook for
  `run_login`. If none exists, cancellation is cooperative: the stepper drops back and
  ignores the eventual result (phase/generation check); the local listener times out on
  its own. No new blocking UI while that happens.
- **Custom widgets**: `WA_StyledBackground` on every custom QWidget subclass that carries
  a QSS background (Qt gotcha).
- **Sizes**: manager 640 wide, stepper 600 wide, natural heights; CSS px map 1:1 to Qt
  logical px.

## 9. Approved gap-fills (spec wins over handoff/codebase here)

1. **Manager capability sentence**: the handoff card shows "Checked 12 minutes ago — can
   list, read, write, compose and delete", but `list_profiles` returns no capability data
   and the API is fixed. The card states only "Checked {relative}"; after an in-session
   `Check now` it shows the real summary from the check response. No invented claims.
2. **Remove confirm**: the `QMessageBox` confirm is replaced by the inline on-card confirm
   region (modal-on-modal ban, RECOMMENDATIONS item 12 applied consistently).
3. **Delete-original notice**: shown only for `service_account_key` creations. Today the
   code appends `COPY_DELETE_ORIGINAL` unconditionally, including after OAuth sign-in
   where no original file exists. Constant untouched; display context only.
4. **Empty manager state**: a single muted "No connections yet." line (not in the handoff).

## 10. Testing

- Five of the six existing tests in `tests/gui/test_connection_dialogs.py` stay green
  **unmodified** — constants verbatim (the contractual-copy test is untouchable),
  payload builders unchanged, `load_key_file` unchanged, `primaryButton` preserved on
  the stepper's key button, health gating still blocks both credential paths.
  Attribute contract this imposes on the rebuilt dialogs: `ConnectionsDialog.new_button`;
  `NewConnectionDialog.key_button` (objectName `primaryButton`), `.signin_button`, and
  `.status_label` — the gate banner's message label keeps the name `status_label` and
  carries `COPY_SERVICE_FIRST` (the existing test waits for "not reachable" in
  `status_label.text()` with both credential buttons disabled).
- **One structural test is explicitly rewritten**:
  `test_connections_dialog_new_button_is_primary` asserts dialog-level `check_button`
  and `remove_button` attributes, which the approved per-card design removes
  (RECOMMENDATIONS item 12: per-card actions, not a selection-driven button bar). It is
  not one of the contractual-copy tests. It is rewritten to assert the new structure:
  `new_button` is `primaryButton`, `close_button` is not, and per-card Check/Remove
  buttons are not (asserted in the new manager tests).
- New tests (GUI, offscreen, QSettings isolated by the existing autouse fixtures in
  `tests/gui/conftest.py`; never the live service):
  - Manager: card rendering per auth type (pills, adc note, amber stale-oauth line,
    relative times, "never"); check-in-progress and in-place rewrite; inline remove
    confirm; 409 refusal parsing (count, no raw string) and `showJobsForProfile` emission;
    empty state; list failure.
  - Stepper: rail state per step; Next gating; step-1 helpers; health gate (banner,
    disabled treatment, `Check again`, no tab-reachable credential action); wrong-file-type
    block + button relabel; sign-in state and Escape cancel; validating probe pacing
    (timer-driven, capped); verified variants (key path + notice vs oauth without);
    failed variants (chips vs before-bucket); `Copy this summary`; `Check the bucket name`
    and `Try another credential` navigation with fields intact; duplicate-name routing to
    step 1; `Add another` reset.
  - Main window: filter set on signal, bar shown, `Show all` clears, first-run gate uses
    unfiltered jobs, poll preserves filter.
  - Wizard: the two helper labels exist with the exact strings.
- `test_no_hex_colors_outside_theme_py` stays green (new modules are flat in `gui/`).
- Suite baseline on master: **636 passed, 13 skipped** (`-o addopts= -q`; the plain `-q`
  full-suite run drops its final summary line on this host — use `-o addopts= -q` or a
  junitxml cross-check; counts are recorded, never estimated).

## 11. Out of scope (deferred, decisions on record)

RECOMMENDATIONS item 1 options 1/2 (capability storage / creation rejection — service
contract); copy rewordings (items 9/10); any service-side per-probe progress signal;
the icon set (parked); Phase 6 packaging; the deferred GUI backlog in project memory.

## 12. Done criteria

- Full suite green: baseline 636/13 plus the new tests, counts recorded from
  `-o addopts= -q` output (or junitxml cross-check).
- Hex grep outside `theme.py` empty; hex acceptance test green.
- Manager and stepper visually match the committed README spec in both themes — manual
  smoke check with the user at the end, GUI launched from the worktree venv against the
  live service **read-only**.
- Contractual-copy tests green and unmodified.
- Merged to master with `--no-ff` and pushed to origin.
