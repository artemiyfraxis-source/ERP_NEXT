---
name: erp-machine-hmi
description: Rules for ERP_NEXT live machine cards and control screens used at the machine (ОБ.019 cut/strip/laser line, ОБ.099 press, Godex marking, VNA bench, any agent-driven equipment) — status, commands, setpoints vs live values, trial-and-correct, threading, marking. Use with erp-screens whenever building, fixing or critiquing such a card («карточка станка», «управление станком», «наладка», «поправка», «ролики», «маркировка на проводе», «не юзер френдли»).
argument-hint: "[build|review] <machine card>"
---

# ERP machine HMI

A machine card is an operator HMI, not an office table: the operator stands at the machine, maybe on the 12.7"
tablet with gloves, one eye on the wire. Principles adapted from high-performance HMI practice (ISA-101 style):
calm by default, colour only for the abnormal, the screen follows the job. `erp-screens` still applies (Russian
words, shots at three sizes, design-critique); where they conflict on a machine card, this file wins.

## The 5-second test (gate for every change)
From one glance the operator answers: **is the machine OK, what will it cut now, what do I do next?** If the
after-shot doesn't answer all three in the top third of the card, it is not done.

## 1. Status first, once
- One status strip at the top: machine state in words («Готов», «Режет 37 из 100», «Авария: нет ответа от
  маркиратора»), link state with age, the next step as a short hint. Nothing repeats it lower down.
- Normal = neutral grey/ink. Amber = needs attention, red = alarm or no link. No decorative colour; a red button
  is not a status.
- No link / no data: one compact line plus the last known values greyed with their time. Never a big empty panel,
  never «на станке —» under every field — one banner says it for all.
- Name the real cause in operator words: «На ПК станка открыт пульт PXS_HMI — закройте его, ERP ждёт» instead of
  `mbd: busy … bus refused`.

## 2. Layout follows the job
Top to bottom: Status/commands → Что режем (ETK job or «Провод вручную») → Параметры (wire drawing) → Записать →
Пуск → Проба и поправка → Запомнить для провода. Expert groups (speeds, middle strip, coiling, marking fonts) are
collapsed; «Подключение» goes last and collapsed.

## 3. Commands
- One row, fixed order; targets ≥ 44 px high, ≥ 8 px apart; no hover-only meaning (touch).
- Every command shows its own result next to the button: «отправлено… / выполнено 16:05 / отказ: станок не в
  ожидании (103)». The journal below is history, not the feedback channel.
- A disabled button says why on the button row («Пуск — сначала Сброс»), not in a tooltip.
- Screen «Стоп» is labelled honestly; the physical E-stop stays primary and the card says so once.
- Latched machine functions (feed rollers, marking on/off) are toggles that show the current state —
  «Ролики подняты · опустить» — read back from the PLC, never a verb with no state.

## 4. Setpoint vs machine
- Each parameter shows what ERP will write and what is on the machine now, side by side; highlight only the
  difference. Units on every number, comma decimals, fixed decimals per field, tabular figures, input width
  fits the value.
- Before any bus write: confirmation listing old → new for the changed fields only.
- Defaults come from data and say where from: «0,79 = Ø жилы 0,64 + 0,15», «из рецепта 18.11», «из ЭТК».

## 5. Trial and correction
- The operator enters only what he measured («Получилось, мм»). The system computes the correction from the value
  that was on the machine (`live − measured`) and shows it in words before saving: «На станке 325,00, получилось
  320,00 → поправка +5,00, следующая запись 330,00». A signed manual offset is an expert option, not the default.
- After saving, everything on screen reloads; nothing shows the pre-correction value.
- Good settings are kept per (machine, wire) with one explicit «Запомнить для провода»; the next time that wire
  is picked it starts from them.

## 6. Words
Operator words, not register or vendor names: no `D136`, `Feed Wheel`, `Dia/Back`, Chinese labels or type codes
on the face. The register may live in a tooltip for the engineer.

## 7. Shots
Besides `erp-screens` shots: one per state — normal, no link (pult open), alarm, running — and the tablet width.
Run the 5-second test on each and say the three answers in the report.
