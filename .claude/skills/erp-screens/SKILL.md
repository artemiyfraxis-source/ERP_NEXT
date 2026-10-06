---
name: erp-screens
description: Mandatory rules and procedure for creating or changing ANY ERP_NEXT screen (React page, card, form, table, ETK view, planning board). Use before touching frontend/src/*.tsx or styles.css, and when the owner sends a screenshot with a UI complaint («шакально», «не читаемо», «кривой вид», «сделай как в гугле»).
---

# ERP_NEXT screens — rules of this production

The owner runs a cable-assembly shop. His yardstick is Google SUPPLYMAP: «твоя задача делать лучше, а не
хуже». **Google SUPPLYMAP is the reference for behaviour and block order only**, not for the look: do not copy
Google's visuals (owner, 2026-09-30: «не тяни дизайн из гугла … придерживайся общего стиля … можно лучше»).
Visuals follow the ERP's own style (section 5) and should read better than the sheet. Every screen is judged by a fitter, a master, a technologist or a buyer who must read it at a glance
on a 27" monitor (2560 px), a 16" laptop (~1536×864 CSS px — owner, 04.10: «на 16 дюймах склад не читаемый») or a
12.7" tablet (~1400 px, touch). Every screen change is checked at all three; key columns fit 1536 px without sideways scroll. Generic design advice
(`design-critique`) applies on top; where it conflicts, this file wins.

## 1. Words on the screen

- **Russian only.** No English identifiers, type codes or internal ids as a title or label: not `cutWireStrip`,
  `insTerm`, not «БЕЗ КОДА», not `undefined/null/NaN`.
  An operation is shown by its Russian name («Резка с разделкой», «Опрессовка терминалов»). The operation-card
  code (`CUT_COAX+2strip`) is allowed as a small grey second line under that name (ETK route list, ETK header,
  template library — owner asked for it 2026-09-30); it is forbidden only instead of the name, and an empty
  code shows nothing (never a placeholder). `shoot.mjs` flags such codes — justify them in the reply.
- Missing name → derive it (template title → category map → operation-card name), do not fall back to the
  type id. Backend source of truth: `production/launchable.py` `OPERATION_TYPE_NAMES` / `stage_name()` /
  `operation_code()` (the master screen already uses them, #128). Frontend has only partial copies
  (`tcGenLogic.ts` `OP_CATEGORY_MAP`, `TechcardsPage.tsx` `RECORD_CATEGORIES`): send the Russian name from the API
  or extend that one map — never add another copy. `workflows/techcards_all.py` still writes «БЕЗ КОДА»; treat it
  as an empty code, never display it.
- Domain words exactly as in SUPPLYMAP: КБ, ЭТК, ТК, ГРН, партия, комплектация, выдача, отгрузка, статусы
  проекта `Новый→ТК готова→КБ набрана→В работе→КБ на складе→Отгружено`, блокеры ХОЛД/СТОП/ОЖИД ИНФ/ОЖИД
  КОМПЛ/ПАУЗА. A button says what it does, verb first («Назначить на 01.10», «Выдать ЭТК»), and keeps the same
  word in the result message.
- No service text: no explanations of how the system works («Маршрут копируется из техкарты…»), no tech notes,
  no emoji on buttons. One short hint line only where the Google screen has one.

## 2. Anything that plans needs a date

Assigning, launching, scheduling, reserving: the user must see and choose **when** (and for how long).
Default = the nearest working day, visible in the control and in the button («Назначить на завтра, 01.10»),
changeable in one click (Сегодня / Завтра / date picker; a week or a range where the action spans days).
Lists of planned work show the date or period in the row. Never an action whose period is implicit.
There is no shared date formatter: dates are shown as `01.10` / `01.10.2026` / `01.10 14:30` (Russian), never
ISO `2026-10-01`. When adding a date display, extract a shared helper instead of a new local `formatDay`.

## 3. Density and layout (owner's corrections, PRs #47–#98)

- Content uses the full width on 27"; no narrow centred column, no empty right third. Forms: even grid,
  4 columns ≥1600 px, 2 on the tablet (`.form-grid`, `.half`, `label.wide`), no holes, no full-width inputs
  for short values.
- Tables are dense like Google Sheets: `DataTable` (≈30 px rows, zebra, sticky header, key column bold,
  column width by content, one search, «Фильтры» button with tags, «Колонки»/«Excel» on the right). Use it
  instead of a hand-made table. A clickable row is clickable as a whole (cursor + hover), not just its name.
- No browser-default look: no bare `fieldset/legend`, no default selects without the app style, no giant
  headings next to tiny text (h1 22, h2 16, body 14, table 13).
- No frame inside a frame inside a frame. No column of identical buttons (14 × «Назначить»): select, then act
  once, or put the action in the row's hover/menu.
- Blocks, cards, tabs keep the order and names of the Google screen unless there is a reason, written in
  `docs/migration/RUNLOG.md`. Their look is the ERP's own (tokens, cards, `DataTable`), not Google's.
- Tablet: touch targets ≥40 px (`pointer: coarse`), sidebar behind «☰», nothing scrolls sideways except a
  wide table inside its own wrapper.

## 4. States

Every async view has three honest states: loading (short, `.empty` «Загрузка…»), empty (says what to do next:
«Нет заданий на 01.10 — назначьте операцию слева»), error (`.error-banner` with `failureText()`; «Нет связи с
ERP» when offline). A spinner that can hang forever is a bug. No fetch on every tab open when the data is
already in the DB.

## 5. Use what exists (don't invent a parallel style)

Tokens in `frontend/src/styles.css` `:root`: `--primary #00b09b`, `--foreground`, `--card`, `--border`,
`--accent`, `--muted-foreground`. Tone classes `.tone-ok/warn/danger/info/neutral` for statuses. Components:
`DataTable`, `ProjectStatus`, `Gantt`, `ToolingBySection`, `print.ts`, `.panel`, `.page-head`, `.seg-tabs`,
`.primary/.secondary/.small`, `.error-banner/.success-banner/.warning-inline`, `.plan-drawer`. `var(--danger)`
and `var(--muted)` are referenced but undefined — use the tone colours, do not add more undefined tokens.
A new colour, radius or font size is a token in `:root`, not a literal in one component.

## 6. ETK operation — fixed block order (owner, 2026-09-30)

1. **Вход** — комплектующие and полуфабрикаты from the previous operation, with quantity and unit.
2. **Оснастка** in three sections: Инструмент · Оборудование · Расходники. For soldering (пайка/лужение) the
   жало and the температурный режим are mandatory lines; if missing in data, show «не задано» in red.
3. **Ход операции** — numbered steps; КОНТРОЛЬ steps highlighted with their допуск.
4. **Типовые дефекты** — each with a «Норма | Брак» picture from the shared library `etkDefectArt.ts` (picked at
   render time by defect name + operation code, so issued ETKs need no reissue), name, cause. A defect wording
   that falls to the «!» sign gets a rule (and a scene if none fits) in that library, never a per-ETK picture.
5. **Эскиз** операции.
6. **Результат** — полуфабрикат (name, qty, unit) and расчётное время.
Parameter chips (аппликатор, высота обжима, отрыв, время) stay in the header. Print (`etkPrint.ts`) follows
the same order.

## 7. Procedure — every screen change

1. **Reference.** The owner's screenshot, or the Google screen from SUPPLYMAP code (`*.html` in SUPPLYMAP
   root; its harness `SUPPLYMAP/_local/tooling/harness/shoot.mjs` renders the portal with a mocked backend).
   Take from it the behaviour and block order, not the look. Write down in 3–5 lines what it does right and
   what we do better.
2. **Demo up (cloud/Linux):** `bash scripts/demo/run-demo.sh` → API + built frontend on
   http://127.0.0.1:8021 over `backend/demo.db` with seeded data (Windows: `scripts/demo/run-demo.ps1`).
   After a frontend edit: `cd frontend && npx vite build`, the API serves the new build.
3. **Shots before:** `node scripts/demo/shoot.mjs <section> ["name=?section=…&project=…"]` → PNGs at 27" and
   tablet in `/tmp/erp-shots` plus a lint of visible text (English ids, raw codes, «без кода», stuck
   loading, sideways scroll). Copy the ones you show to `/mnt/project-files/erp/<topic>/before-*.png`.
4. **Change**, then **shots after**. LOOK at every PNG (Read tool): exit 0 proves nothing. Run the
   `design-critique` skill on the after-shots against the reference and fix every critical/moderate finding.
   Every lint finding is fixed or justified in the reply.
5. `npm run check`, `erp-verifier`, merge. Reply to the owner with before/after links, 2–5 Russian lines.

Hub: [[erp-next-hub]] · memory note `erp-screens-rules`.
