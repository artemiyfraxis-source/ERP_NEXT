---
name: erp-dense-ui
description: Style rules for ERP_NEXT's dense operator screens — planning boards, registers, warehouse and kitting tables, Gantt-like load charts. Use with erp-screens when building or polishing a data-heavy screen («плотная таблица», «как в гугле», «план», «загрузка цеха», «реестр»), and as the yardstick when critiquing one.
argument-hint: "[build|review|polish] <screen>"
---

# ERP dense UI

Adapted from educlopez/ui-craft `ui-craft-dense-dashboard` (MIT, commit ceecc8e): data-first, keyboard-first
operator tools. The `erp-screens` skill wins on any conflict (Russian labels, visible date/period, fixed ETK
block order, before/after shots). Don't ask style questions — these knobs are fixed.

## Knobs (fixed)
- Craft 7: polished, not precious — speed over sheen.
- Motion 3: micro only — row hover, selected row, inline save. No entrance/scroll animations in tables.
- Density 9: 4/8 px grid; whitespace must earn its place.

## Type
- System UI sans (current `styles.css` stack) 400/500; mono only for IDs (ГРН, REQ…, артикулы), timestamps, codes.
- Body 13–14 px; 12 px inside dense tables is fine; never below 11 px for anything a fitter must read.
- `font-variant-numeric: tabular-nums` on every number column, total, date, duration; IDs `tabular-nums slashed-zero`.
- Page title 18–20 px; section 14–15 px 500. Headers of table columns: 12 px, muted, never bold-black.

## Color
- One brand accent (existing teal) — primary action, active tab, selected-row tint, today marker.
- Semantic, subdued: success / warning / danger / info at lightness 50–60 %, chroma ≤ 0.15. Never saturated —
  the screen is on for a whole shift. Status text and dots carry the color; cell backgrounds stay neutral
  except for deliberate load heatmaps.
- A legend for every color that encodes meaning, placed next to the thing it explains.

## Spacing & layout
- Scale 4 / 8 / 12 / 16 / 24 px — never 5, 7, 9, 10.
- Table cells: 4–6 px vertical, 8–12 px horizontal; row height 28–32 px (Google Sheets ≈ 21–25 px is the floor).
- Toolbars: 8 px gaps, 32–36 px controls. Cards: 16 px padding, 8 px radius, 1 px border instead of shadow.
- Wide boards scroll inside their panel, with the identifying columns (№, заказчик) sticky on the left and the
  date header sticky on top; the page itself never scrolls sideways.

## Data display
- Numbers right-aligned + tabular. Empty value = «—» muted, not blank and not 0.
- Every timeline shows its period in words and dates; weekends and today are visibly marked.
- Status = short Russian word in a subdued chip; same status → same chip everywhere in the app.
- Category comparison: horizontal bars. Trend: line/area. No pies, donuts or 3D.
- Deltas: plain text in semantic color, no pill. Empty state: one line + the action that fills it.
- Truncated text gets a tooltip with the full value; never clip a project number.

## Chrome & navigation
- Breadcrumbs + tabs + toolbar + filter chips together are fine.
- Filter chips: outline, removable ×. Search field says what it searches («№ проекта или заказчик»).
- Keyboard: Ctrl+K search where present, Enter opens a row, Esc closes dialogs; show hints inline.
- Loading: skeleton rows matching the columns, not a generic spinner.

## Anti-patterns
- Airy card grids for tabular data; full-dark sidebar; saturated colors; proportional digits in columns.
- Colored pill on every value; uniform colored top borders on cards.
- Hidden meaning: icons or colors without a legend or tooltip; English or type codes in labels.
- Scroll-triggered reveals or staggered animations in tables.
