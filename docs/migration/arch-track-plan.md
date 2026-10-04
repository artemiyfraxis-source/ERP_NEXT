# ARCH-TRACK plan — Vasily's model in ERP (2026-10-04)

Direction: ARCHITECTURE.md «Направление: один проект — один трек». Queue rows ARCH-TRACK-1…3 in BACKLOG.md.
Source of the rules: Vasily Kostromin's prototype PRODUCTION_PLANNER (github SaveOurSouls/PRODUCTION_PLANNER,
commit 46f0f70, 23.09: src/domain/types.ts, graph.ts, scheduler.ts, calendar.ts, idle.ts). SUPPLYMAP has no such rule.
Every phase starts only after Vasily answers the questions below. Effort Extra for A and B.

## Phase A = ARCH-TRACK-1: route graph in the techcard generator (no migration)
Model: Stage = one semi-finished node {key, name, ops:[op keys], final}; 1 op = 1 stage by default, a group op = a stage
holding several generator ops. Edge = {from, to, per_unit, transfer_batch}. Stored as ETK `document.route`
(ETK_VERSION 2→3) + optional `build_config.route` override; without override derived from `build_config.ops` by the
`previous_result` logic of `build_etk` (etk_builder.py:784). cutTut→insTut/endCap is already a side branch → real DAG.
Old cards without route: derived on read.
- A1 (opus) techcards/domain/route.py (new) + etk_builder.py: build_etk writes document.route; semis rows/result names
  from graph predecessors. Gate: tests/techcards/test_route.py (one final, acyclic, all reach final, group ops) and a
  golden test — build_etk over every fixture in test_etk_builder.py + test_techcards_all.py byte-identical except `route`.
  Read: etk_builder.py:704-805, `_topology` :636. Exclude route from the revision fingerprint (no new revisions).
- A2 (sonnet, parallel) production/launchable.py: route_from_document also returns stage_key + inputs per step;
  production still uses positions. Gate: test_launchable.py + a branched case.
- A3 (sonnet, after A1) TcGenerator.tsx + RouteEditor.tsx + api rebuild payload: step «Схема полуфабрикатов» (inputs per
  stage, merge ops into a group, «Готовое изделие»); linear untouched. erp-screens, 27" shot.
- A4 (sonnet, after A1) ProjectEtk.tsx: DAG strip «Маршрут» only when the route branches; linear ETK identical.

## Phase B = ARCH-TRACK-2: production executes the graph
- B1 (opus, migration 0080 after current head) production_operations.stage_key + production_operation_inputs
  (operation_id, input_operation_id, per_unit, transfer_batch). Backfill = today's chain exactly (position-1, per_unit 1);
  final op = op with no consumers. Position stays display/topological order. Gate: upgrade test on a SQLite copy.
- B2 (opus, after B1) execution.py, traceability.py, quality.py, genealogy.py, api.py:470-560: start_work takes boxes from
  any predecessor (input_error by allowed op ids, not previous_position, execution.py:215); consumption = qty × per_unit
  per edge; assembly needs every input; source ops take issued materials; QC/finished goods check «final op», not
  max(position). Gate: existing linear tests unchanged + a two-branch merge case.
- B3 (opus, parallel to B2) scheduling.py, dispatch.py: topological order, start = max over predecessors, reflow pushes
  successors; batch variants and shares kept. Keep B2 out of application.py.
- B4 (sonnet) ProductionPage/ProductionPlanPage/Gantt: inputs per op («из этапов …»), Gantt arrows. erp-screens.

## Phase C = ARCH-TRACK-3: arbiter + project track
- C1 (sonnet) workflows/project_track.py + read ports per module (audit_events has no project column — no audit scan).
- C2 (opus, migration 0081) workflows/arbiter.py + outbox registration: rules {trigger → module task}, idempotent by
  outbox key; first triggers: техкарта выпущена → план производства; комплект выдан → задание запускаемо;
  ГП принята → отгрузка. Must not duplicate procurement_stage / receipt_stage.
- C3 (sonnet) ProjectCard.tsx + ProjectTrack.tsx. erp-screens.

## Optional, one slice each, after C
Machines with unit count; per-person calendar; idle breakdown (idle.ts reasons); baseline vs actual; WIP buffer;
norm types linear/static/variable.

## Вопросы Василию до фазы A
1. Групповая операция — это один этап с несколькими операциями или одна операция над несколькими проводами?
   (в прототипе Stage держит несколько RouteOperation, а правило — «1 операция = 1 полуфабрикат»)
2. Расход на единицу для резки: коробка этапа резки считает провода или комплекты на жгут?
3. Кто рисует граф: выводится из топологии разъёмов автоматически или технолог правит руками?
4. Передаточная партия — на каждое ребро (как в прототипе) или одна на задание (как в ERP сейчас)?
5. Материалы по этапам: каждый исходный этап берёт свои строки комплекта, или весь комплект идёт на первый этап?
6. Режимы «бригада / независимые» и число людей на операцию ещё нужны, если в ERP уже есть доли (бригада / штуки)?
7. Полуфабрикаты со склада (остатки, поставки на этап) — в объёме или только готовые изделия?
8. Выбор сценария «первая отгрузка / пропускная способность» и варианты по Парето — заменить варианты партий ERP
   или добавить к ним?

Decided by agent («решено агентом — проверить»): graph lives in the ETK JSON, no Phase A migration (old cards stay
readable, generator output stable); Phase C reads modules through ports, not an audit backfill.
