# Design tokens — MML Cloud Courier

Every color in the design comes from this table. Nothing is hard-coded anywhere else, including
the service banner (which currently carries `#f2dede` / `#a94442` inline in `main_window.py`).

Values are given as hex for direct use in QSS, with the authoring `oklch()` beside them. The
oklch values are the source of truth for deriving new colors — they keep lightness perceptually
even across hues, which is why the dark palette stays legible without re-tuning by eye.

## Surfaces

| Token | Light | Dark | Used for |
|---|---|---|---|
| `titlebar` | `#eef1f4` | `#070d12` `oklch(.155 .014 245)` | Window title bar |
| `chrome` | `#fafbfc` | `#0b1116` `oklch(.175 .014 245)` | Toolbar, tab strip |
| `rail` | `#ffffff` | `#161d22` `oklch(.225 .015 245)` | Job rail background |
| `bg` | `#f4f6f8` | `#11171c` `oklch(.20 .014 245)` | Content area behind cards |
| `surface` | `#ffffff` | `#1a2128` `oklch(.245 .016 245)` | Cards, table rows, inputs |
| `track` | `#e3e9ec` `oklch(.93 .008 232)` | `#2d343a` `oklch(.32 .014 245)` | Progress troughs, segmented-control well, *Waiting* state |
| `skip` | `#c5d0d6` `oklch(.85 .015 232)` | `#42525f` `oklch(.43 .03 245)` | *Skipped (already up to date)*, queued dots |

Dark surfaces carry a slight blue cast (chroma .014–.016 at hue 245) rather than being neutral
gray. That is deliberate: it keeps the dark build recognizably the same product as the light
one, and it stops the blue accent from looking like it is floating on a foreign background.

## Text

| Token | Light | Dark | Used for |
|---|---|---|---|
| `ink` | `#12181f` | `#eaeff4` `oklch(.95 .008 245)` | Primary text, values, selected rail rows |
| `muted` | `#4d5762` | `#a8afb5` `oklch(.75 .012 245)` | Secondary text, labels, unselected rows |
| `faint` | `#8a94a0` | `#7a8188` `oklch(.60 .014 245)` | Field labels, timestamps, metadata |
| `disabled` | `#b9c2cb` | `#474e54` `oklch(.42 .014 245)` | Unavailable controls |

`ink` on `surface`: 15.6:1 light, 13.1:1 dark. `muted` on `surface`: 7.9:1 light, 6.5:1 dark.
`faint` on `surface`: 3.4:1 light, 3.6:1 dark — **`faint` is for supporting metadata at 10.5px+
only**, never for anything a user must read to make a decision. Everything load-bearing uses
`muted` or `ink`.

## Lines

| Token | Light | Dark | Used for |
|---|---|---|---|
| `line` | `rgba(18,24,31,.12)` | `rgba(160,195,235,.13)` | Card borders, dividers, panel edges |
| `hairline` | `rgba(18,24,31,.06)` | `rgba(160,195,235,.07)` | Table row separators |

Both are alpha, not solid, so they sit correctly on any surface in the stack.

## Accent — ocean blue

| Token | Light | Dark | Used for |
|---|---|---|---|
| `accent` | `#006ea0` `oklch(.50 .13 232)` | `#2eabe1` `oklch(.70 .13 232)` | Primary button, active tab underline, selected row border, *Verified* |
| `accent_2` | `#4caad7` `oklch(.70 .11 232)` | `#1d85b0` `oklch(.58 .11 232)` | In-flight progress, *Checking*, per-file bars |
| `accent_3` | `#95d4f6` `oklch(.84 .08 232)` | `#77c9f3` `oklch(.80 .10 232)` | *Transferring* |
| `accent_text` | `#005682` `oklch(.42 .12 232)` | `#67c4f2` `oklch(.78 .11 232)` | Accent-colored text, *Running* group header |
| `accent_soft` | `#e5f5fd` `oklch(.96 .02 232)` | `rgba(120,175,255,.10)` | Pill background, badge fills |
| `accent_edge` | `#c8dfeb` `oklch(.89 .03 232)` | `rgba(120,175,255,.20)` | Pill border |
| `accent_ink` | `#ffffff` | `#04111c` `oklch(.17 .03 245)` | Text on a filled accent button |
| `rail_selected` | `#eaf6fc` `oklch(.965 .015 232)` | `rgba(120,175,255,.09)` | Selected rail row background |

Note that `accent_ink` is **dark** in dark mode: the dark accent is light enough that white text
on it fails contrast. A filled button in dark mode is a light blue chip with near-black text.

`accent` on `surface`: 5.3:1 light, 6.4:1 dark. `accent_text` on `surface`: 8.3:1 / 8.8:1.

## Danger — failure only

| Token | Light | Dark |
|---|---|---|
| `danger` | `#c13c3b` `oklch(.55 .17 25)` | `#e8605b` `oklch(.66 .17 25)` |
| `danger_soft` | `#ffe9e6` `oklch(.96 .035 25)` | `rgba(255,140,120,.12)` |
| `danger_edge` | `#facfca` `oklch(.89 .05 25)` | `rgba(255,140,120,.22)` |
| `danger_text` | `#892122` `oklch(.42 .14 25)` | `#ff9c8e` `oklch(.80 .13 28)` |

**Red is reserved for failure.** Not for delete buttons, not for required fields, not for the
Cancel control. The whole point of the state strip and the rail's *Needs attention* group is
that red means one thing, so a user scanning at 8am finds it without reading.

`danger_text` on `surface`: 8.7:1 light, 7.4:1 dark. `danger` on `surface`: 4.9:1 / 5.6:1.

## Warning — self-clearing problems

| Token | Light | Dark |
|---|---|---|
| `warn` | `#b06f35` `oklch(.60 .11 60)` | `#e8a95c` `oklch(.78 .12 70)` |
| `warn_soft` | `#feefdc` `oklch(.96 .03 75)` | `rgba(255,200,120,.12)` |
| `warn_text` | `#774500` `oklch(.44 .10 65)` | `#eeba70` `oklch(.82 .11 75)` |

Used for paused jobs, retrying files, stalled state, and the *Retries on its own* tag — things
that are not right but need no human. Keeping these amber rather than red is what lets the two
genuinely blocking causes on the Errors tab stand out.

## State colors, in state order

The stacked bar and legend always run in this order, which matches a file's life cycle:

| State (from `format.py`) | Color |
|---|---|
| Verified | `accent` |
| Checking | `accent_2` |
| Transferring | `accent_3` |
| Waiting | `track` |
| Skipped (already up to date) | `skip` |
| Failed | `danger` |
| Excluded after repeated failures | `danger` (with `danger_text` label) |

## Typography

Windows targets: **Segoe UI Variable** (interface) and **Cascadia Mono** (numerics, paths,
labels). The prototypes use Instrument Sans and Geist Mono as the closest available equivalents.

| Role | Size / weight | Tracking | Notes |
|---|---|---|---|
| Screen heading | 21–22 / 600 | −.02em | First run, Summary verdict |
| Job name | 18 / 600 | −.015em | Progress headline |
| Big numeric | 26 / 600 mono | −.02em | Percentage |
| Stat value | 19 / 600 mono | −.01em | Summary cells |
| Card title | 14–14.5 / 600 | −.005em | Error cause message |
| Body | 13 / 400, 1.5 | — | Explanatory sentences |
| Control label | 12.5 / 500 | — | Buttons, tabs, toolbar |
| Row text | 12.5 / 500 | — | Rail job names |
| Secondary | 11.5–12 / 400 | — | Counts, legends, states |
| Metadata mono | 10.5–11.5 / 400 mono | — | Paths, times, details |
| Section label | 10.5 / 400 mono | +.08em | UPPERCASE card headers |
| Group header | 10.5 / 600 mono | +.09em | UPPERCASE rail groups |

Everything numeric is mono. Percentages, rates, byte counts, file counts, timestamps, checksums
and paths all sit in tabular figures so they stop jittering between polls.

## Spacing

A 4px base. Values in use: `2 · 4 · 6 · 7 · 8 · 9 · 10 · 11 · 13 · 14 · 15 · 16 · 18 · 20 · 22 · 26`.

| Context | Value |
|---|---|
| Content padding | `18 20` |
| Card padding | `13 15` to `15 17` |
| Card gap | 15 |
| Toolbar padding / gap | `0 14` / 10 |
| Rail row padding | 8, with 12px outer |
| Table row padding | `8 20` |
| Legend gaps | `8 18` |
| Button padding | `6 11` small, `7 13` medium, `9 16` primary |

## Radii

| Value | Used for |
|---|---|
| 2 | State swatches |
| 3–4 | Progress bars, segmented-control segments |
| 6 | Buttons, inputs, rail rows |
| 7 | Primary buttons |
| 9 | Cards |
| 10 | Window |
| 999 | Status pill, step badges |

## Elevation

One shadow, on the window only: `0 12px 34px rgba(0,0,0,.14)`. Cards are separated by their 1px
`line` border and surface contrast, never by shadow. In dark mode, drop the shadow entirely and
rely on the border — shadows are invisible on dark grounds and only muddy the edges.

## Column widths that are not negotiable

| Column | Width | Why |
|---|---|---|
| Files → State | 204px | *"Excluded after repeated failures"* must render in full at 11.5px, or it becomes indistinguishable from *Failed* |
| Rail | 262px | `#118 Cetacean_PAM_July_wav` fits without eliding at 12.5px |
| Events → kind | 52px | Aligns `verified` / `failed` / `retry` into a scannable column |
| Summary → state label | 200px | *"Excluded after repeated failures"* again |
