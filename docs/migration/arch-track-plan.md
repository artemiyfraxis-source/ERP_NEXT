# ARCH-TRACK plan — Vasily's model in ERP (2026-10-04)

Direction: ARCHITECTURE.md «Направление: один проект — один трек». Queue rows ARCH-TRACK-1…3 in BACKLOG.md.
Source of the rules: Vasily Kostromin's prototype PRODUCTION_PLANNER (github SaveOurSouls/PRODUCTION_PLANNER,
commit 46f0f70, 23.09: src/domain/types.ts, graph.ts, scheduler.ts, calendar.ts, idle.ts) and his voice notes of
04.10 (below). SUPPLYMAP has no such rule. Effort Extra for A and B.

## Vasily's answers (voice notes forwarded by the owner, 2026-10-04 17:48)
- The generated sequence of operations is split into interconnected **blocks** («квадратики»): e.g. нарезка →
  опрессовка провода → монтаж → маркировка, or нарезка → пайка → одевание термоусадок. A block = one semi-finished
  product. A block may hold one operation (нарезка) or several (комплексная сборочная работа). The technologist opens a
  block and configures its operations the way the generator does today — not one flat list («не сплошняком»).
- The block scheme is what goes into **planning**: a network schedule (сетевой график, classic PM/quality scheme).
  Techcards stay as they are — print / tablet / accompanying sheet, they end at the operator.
- The scheme's result: **total time and sum of incoming tech operations per one semi-finished product**, plus the
  **number of output semi-finished products and the number of input semi-finished products**.
- Input quantity per one semi-finished product is set **by hand** on the scheme for now; later it may be automated
  (from the graphics: which components a block uses). Components per block matter — keep a place for them so they can
  be stitched to the kit later.
- **Default: one block always outputs one semi-finished product** — output quantity can be ignored for now.

## Phase A = ARCH-TRACK-1: block scheme in the techcard generator (no migration)
Model: Block (Stage) = one semi-finished node {key, name, ops:[op keys], final, components:[kit line refs, optional]};
a block holds 1..n generator ops. Edge = {from, to, per_unit (manual, default 1)}. Output per block = 1 (fixed for now).
Block totals: time = sum of its ops' norms; rolled-up time per one final item = block time + Σ(per_unit × input time).
Stored as ETK `document.route` (ETK_VERSION 2→3) + `build_config.route` override (hand-edited scheme); without override
derived from `build_config.ops` by the `previous_result` logic of `build_etk` (etk_builder.py:784), one op per block.
cutTut→insTut/endCap is already a side branch → real DAG. Old cards without route: derived on read.
- A1 (opus) techcards/domain/route.py (new) + etk_builder.py: build_etk writes document.route with block totals; semis
  rows/result names from graph predecessors. Gate: tests/techcards/test_route.py (one final, acyclic, all reach final,
  multi-op blocks, totals) and a golden test — build_etk over every fixture in test_etk_builder.py +
  test_techcards_all.py byte-identical except `route`. Read: etk_builder.py:704-805, `_topology` :636. Exclude route
  from the revision fingerprint (no new revisions).
- A2 (sonnet, parallel) production/launchable.py: route_from_document also returns block key + inputs per step;
  production still uses positions. Gate: test_launchable.py + a branched case.
- A3 (sonnet, after A1) TcGenerator.tsx + RouteEditor.tsx + api rebuild payload: step «Схема полуфабрикатов» —
  blocks as boxes, arrows between them, per_unit on an arrow, open a block to configure its operations as today,
  totals (time, inputs) per block. Linear untouched. erp-screens, 27" shot.
- A4 (sonnet, after A1) ProjectEtk.tsx: block strip «Маршрут» only when the route branches; linear ETK identical.

## Phase B = ARCH-TRACK-2: production plans and executes the block network
- B1 (opus, migration after current head 0079) production_operations.stage_key + production_operation_inputs
  (operation_id, input_operation_id, per_unit, transfer_batch). Backfill = today's chain exactly (position-1, per_unit 1);
  final op = op with no consumers. Position stays display/topological order. Gate: upgrade test on a SQLite copy.
- B2 (opus, after B1) execution.py, traceability.py, quality.py, genealogy.py, api.py:470-560: start_work takes boxes from
  any predecessor (input_error by allowed op ids, not previous_position, execution.py:215); consumption = qty × per_unit
  per edge; assembly needs every input; source blocks take issued materials; QC/finished goods check «final op», not
  max(position). Gate: existing linear tests unchanged + a two-branch merge case.
- B3 (opus, parallel to B2) scheduling.py, dispatch.py: network schedule — topological order, start = max over
  predecessors, reflow pushes successors; batch variants and shares kept. Keep B2 out of application.py.
- B4 (sonnet) ProductionPage/ProductionPlanPage/Gantt: inputs per op («из блоков …»), arrows. erp-screens.

## Phase C = ARCH-TRACK-3: arbiter + project track
- C1 (sonnet) workflows/project_track.py + read ports per module (audit_events has no project column — no audit scan).
- C2 (opus, migration) workflows/arbiter.py + outbox registration: rules {trigger → module task}, idempotent by outbox
  key; first triggers: техкарта выпущена → план производства; комплект выдан → задание запускаемо;
  ГП принята → отгрузка. Must not duplicate procurement_stage / receipt_stage.
- C3 (sonnet) ProjectCard.tsx + ProjectTrack.tsx. erp-screens.

## Optional, one slice each, after C
Machines with unit count; per-person calendar; idle breakdown (idle.ts reasons); baseline vs actual; WIP buffer;
norm types linear/static/variable; components per block from the graphics (Vasily: «потом сошьём»).

## Вопросы Василию — закрыто 04.10
1. Групповая операция = блок с несколькими операциями. 2. Блок выдаёт 1 полуфабрикат; сколько входящих на один —
   вручную на схеме. 3. Схему правит технолог руками, автоматизация потом.

## Вопросы Василию — ещё открыты (до фазы B)
4. Передаточная партия — на каждое ребро (как в прототипе) или одна на задание (как в ERP сейчас)?
5. Материалы по блокам: каждый исходный блок берёт свои строки комплекта, или весь комплект идёт на первый блок?
6. Режимы «бригада / независимые» и число людей на операцию ещё нужны, если в ERP уже есть доли (бригада / штуки)?
7. Полуфабрикаты со склада (остатки, поставки на блок) — в объёме или только готовые изделия?
8. Выбор сценария «первая отгрузка / пропускная способность» и варианты по Парето — заменить варианты партий ERP
   или добавить к ним?

Decided by agent («решено агентом — проверить»): graph lives in the ETK JSON, no Phase A migration (old cards stay
readable, generator output stable); Phase C reads modules through ports, not an audit backfill.
