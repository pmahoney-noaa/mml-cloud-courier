# UI/UX recommendations — MML Cloud Courier

Ranked by value against the three stated problems: *hard to tell what's happening during a
transfer*, *too many steps to start a job*, and *looks dated*. Each item carries a rough cost
so you can cut from the bottom.

Cost key: **S** = a few hours, restyle or reorder only · **M** = a day or two, new widget or
reworked layout · **L** = a week-ish, touches the service contract or the wizard flow.

---

## 1. Show every file's state at once, not just a percentage — **M**

*Problem it solves: hard to tell what's happening.*

A single percentage cannot distinguish a job that is 62% done from a job that is 62% done with
2,104 files silently skipped and 11 failed. Add the **"Every file, by state"** card to the
Progress tab: one stacked bar over the eight states from `format.py`, plus a legend with counts.

The data already exists — the state counts drive the tab's own numbers. This is the highest-value
change in the package, because it converts a number a user has to trust into a picture they can
verify.

Specifically, it makes *Skipped (already up to date)* visible. Right now a resumed job that
skips 2,000 unchanged files looks identical to a job that lost them.

---

## 2. Put the close-to-tray guarantee in the window — **S**

*Problem it solves: hard to tell what's happening.*

`tray.py` shows `STILL_RUNNING_MESSAGE` as a balloon the first time the window is closed. Balloons
are missed, suppressed by Focus Assist, and never seen again. Meanwhile the entire architecture —
a Windows service doing the work — depends on the user believing that closing the window is safe.

Add the persistent status pill to the toolbar: *"Service running — transfers continue if you
close this window"*. It costs 40px of toolbar and answers the question permanently. When the
service is down the same pill turns red and reads *"Service stopped — nothing is moving"*, so
one element covers both halves of the truth.

---

## 3. Make the whole window honest when the service is down — **S**

*Problem it solves: hard to tell what's happening.*

Today the banner appears but the toolbar stays fully enabled: `New transfer`, `Pause`, `Resume`
and `Cancel` all look pressable while nothing can happen. A user clicking `Pause` on a stalled
job gets no feedback and reasonably concludes the app is broken.

When the service is unreachable: dim the transport controls to `disabled`, flip the pill to its
danger state, and change the running job's rail status to *"Stalled — service stopped"*. Keep the
banner's `Start the service` button as the only live control. Same treatment on first run, where
`New transfer` leads to a wizard that cannot complete without a connection.

---

## 4. Collapse the four-page wizard to one screen — **M**

*Problem it solves: too many steps to start a job.*

`wizard.py` is four `QWizardPage`s: direction → connection → folders → options/review. For a
scientist who runs one transfer a week, that is three Next clicks and a Finish to express two
facts: *this folder* and *that bucket*.

One page holds all of it comfortably at 1100px:
- Direction as a two-segment toggle at the top, with the existing explanatory sentence beside it.
- Source and destination side by side — the folder drop target on the left, the connection and
  prefix on the right.
- The scan preview inline beneath both, running live as it already does.
- The job name pre-filled exactly as `options_page` does today (`{leaf}-{date}`).
- `Start later` and `Also compute SHA-256 audit hashes` behind a **More options** disclosure —
  they matter to perhaps one job in twenty.
- One `Start transfer` button.

The submission payload is unchanged; this is a layout and validation reshuffle, not a protocol
change. Keep every validation rule, including the mapped-drive warning, which should sit
inline under the folder field where the problem is rather than in a status label at the bottom.

**Then add a drop target.** The most common case is "this folder, the usual bucket". Accepting a
folder dropped anywhere on the window — pre-filling source, last-used connection, and derived
name, leaving only Start — turns the most frequent job into one gesture. Qt: `setAcceptDrops(True)`
plus `dragEnterEvent`/`dropEvent` on the main window.

---

## 5. Surface the error taxonomy instead of burying it — **M**

*Problem it solves: hard to tell what's happening.*

`core/errors.py` and `errors_model.py` already do the hard part: group failures by cause, and
attach a plain-language message and a prescribed action to each. **This is the best asset in the
application** and today it lives inside a collapsed `QTreeWidget` where the action text only
appears after a user selects a node.

Show causes as cards, always expanded, each with its message, count, action, and its own three
buttons. Order them so causes needing a human come first and self-clearing ones last, and tag
them accordingly (`Needs you` / `Retries on its own`). A user should be able to tell, without
clicking anything, whether they have work to do.

Two supporting changes:
- **Per-card buttons, not one shared bar.** A single `Retry these files` button that acts on the
  current selection is ambiguous when four causes are on screen.
- **Colour the self-clearing causes amber, not red.** If all four causes are red, the two that
  need action do not stand out — which defeats the grouping.

---

## 6. Give the rail status colour and hierarchy — **S**

*Problem it solves: looks dated, hard to tell what's happening.*

`jobs_model.py` already pins *Needs attention* to the top. Finish the idea: colour each group
header by severity (red / accent / neutral), put the count beside it, and give each job row a
status dot plus a second line with the actual status — *"Incomplete — needs attention"*,
*"Queued — starts Aug 09 02:00"*. Show the job id inline (`#121 IceSeal_Survey_2026_Leg3`) so the
rail matches what logs and reports say.

The two-line row costs vertical space, and it is worth it: the rail becomes the app's status
summary rather than a list of names.

---

## 7. Elide paths from the left, everywhere — **S**

*Problem it solves: hard to tell what's happening.*

`leg3\imagery\2026-08-04_transect_north\IMG_20260804_1147.tif` truncated from the right reads
`leg3\imagery\2026-08-04_tra…` — every visible character is shared with every other file in the
job. Truncated from the left it reads `…north\IMG_20260804_1147.tif`, which identifies the file.

`Qt.ElideLeft` in every delegate and label that shows a path. Full path in the tooltip. This is
a one-line change per widget and it materially improves the Files tab, the in-progress list, and
the error file samples.

---

## 8. Follow the Windows theme — **M**

*Problem it solves: looks dated.*

Covered fully in `README.md`. Worth noting the bug it retires: the service banner currently pins
both its colors inline with a comment explaining that a background-only stylesheet inherits white
text under Windows dark mode. Token-driven theming makes that class of bug structurally
impossible, and the dark build is the single most visible signal that the app is current.

---

## 9. Use tabular figures for everything numeric — **S**

*Problem it solves: looks dated, hard to tell what's happening.*

Percentages, rates, counts, sizes and timestamps update on every poll. In a proportional face the
digits change width and the whole line twitches. In Cascadia Mono (or any tabular-figure face)
they update in place. It is a font choice on perhaps a dozen labels and it makes a polling UI feel
settled rather than nervous.

---

## 10. Say what is loaded in a virtualized table — **S**

*Problem it solves: hard to tell what's happening.*

`files_model.py` pages rows in as the user scrolls. A 61,000-row job that shows 200 rows and then
appears to stop looks broken. Put `14,208 files · showing 1–200` in the header, and a quiet
loading indicator while a page is in flight.

---

## 11. Realignment notes — **S** each

Small placement changes, listed together because none needs its own argument:

- **Primary action left, in the toolbar.** `New transfer` is the only filled button in the chrome;
  everything else is outline or text. One filled button per region is the rule throughout.
- **Transport controls as one segmented group.** `Pause / Resume / Cancel` currently read as three
  peers scattered in the toolbar; grouping them in a single well says they are three states of one
  control, and makes the unavailable ones obviously unavailable.
- **Status pill right, before the divider.** Persistent state belongs on the opposite side from
  actions, so the eye is not asked to re-scan the same region for both.
- **Percentage right-aligned on the headline row**, baseline-matched to the job name. It is the
  single number people look for; giving it the far-right anchor at 26px makes it findable without
  a label.
- **Card actions bottom-right, destructive-adjacent actions left of them.** On the Errors cards,
  `Retry these files` (the expected action) sits leftmost with the filled treatment, and
  `Stop retrying` — which permanently excludes files — sits beside it as an outline, never as the
  visually dominant control.
- **Summary's follow-up actions bottom-right**, after the explanatory sentence, so the sequence
  reads verdict → numbers → detail → what to do next.
- **Column order on Files: Path, Size, State, Detail.** Path is what identifies a row and takes the
  flexible width; State needs a fixed 204px so the two long strings survive; Detail takes what is
  left and elides.
- **Right-align every numeric column**, left-align every text column, and never centre either.

---

## 12. Things deliberately not recommended

- **No dashboard of aggregate charts.** The app has one job at a time in focus; throughput history
  across all jobs is a report, not a screen.
- **No progress animation between polls.** Interpolating a bar between two poll values invents data
  and makes a stalled transfer look healthy for up to a poll interval.
- **No emoji or illustrated empty states.** The first-run screen carries three sentences and three
  steps because that is what a new user needs; decoration would only add to what they must read.
- **No colour beyond the four families here** (neutral, accent, warn, danger). Every additional hue
  costs the two that must be unmistakable.
