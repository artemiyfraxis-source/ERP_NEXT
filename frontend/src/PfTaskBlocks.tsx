import { useEffect, useState } from "react";
import { productionApi, type ProductionBox, type ProductionOperation, type ProjectTechcard } from "./api";
import { parseDefects } from "./etkOperation";
import { etkOpRuns } from "./etkRoute";
import "./pfTask.css";

/** Задание по ПФ (13.10): у операции задания есть операции ЭТК. Пусто — прежняя операция с одной операцией ЭТК. */
export function isPfOperation(operation: Pick<ProductionOperation, "etk_ops">): boolean {
  return (operation.etk_ops?.length ?? 0) > 0;
}

/** «ПФ-3 · Опрессовка»; обычная операция — её название как раньше. */
export function operationName(operation: Pick<ProductionOperation, "etk_ops" | "position" | "stage">): string {
  const stage = operation.stage || `Этап ${operation.position}`;
  return isPfOperation(operation) ? `ПФ-${operation.position} · ${stage}` : stage;
}

/** «оп. 05·06·07» — номера операций ЭТК этого ПФ по порядку; обычная операция — пусто. */
export function pfOpsText(operation: Pick<ProductionOperation, "etk_ops">): string {
  const ops = operation.etk_ops ?? [];
  // Подряд — диапазоном: «оп. 15–21», а не семь номеров через точку (владелец 20.10: «сложно для восприятия»).
  return ops.length ? `оп. ${etkOpRuns(ops.map((item) => item.position), (value) => String(value).padStart(2, "0"))}` : "";
}

type Requirement = { position: number; controls: string[]; defects: string[] };

type CardOperation = { position?: number; card?: null | { defects?: string; steps?: Array<{ kind?: string; text?: string; tolerance?: string }> } };

/** Требования операций ПФ из ЭТК: шаги контроля с допусками и типовые дефекты, по номеру операции. */
export function requirementsOf(card: ProjectTechcard, positions: number[]): Requirement[] {
  const list = Array.isArray(card.document?.operations) ? card.document.operations as CardOperation[] : [];
  return positions.map((position) => {
    const found = list.find((item) => item.position === position) ?? list[position - 1];
    const steps = found?.card?.steps ?? [];
    const controls = steps
      .filter((step) => step.kind && step.kind !== "Операция" && step.text)
      .map((step) => `${step.text}${step.tolerance && step.tolerance.trim() !== "-" ? ` (допуск: ${step.tolerance})` : ""}`);
    const defects = parseDefects(found?.card?.defects ?? "").groups.flatMap((group) => group.items.map((item) => item.name));
    return { position, controls, defects };
  }).filter((item) => item.controls.length || item.defects.length);
}

/** Блоки карточки монтажника по ПФ: что получить от предыдущей задачи и требования к операциям ПФ (свёрнуты). */
export function PfCardBlocks({ taskId, operation, onBehalfOf = "", showInputs }: {
  taskId: string; operation: ProductionOperation; onBehalfOf?: string; showInputs: boolean;
}) {
  const [boxes, setBoxes] = useState<ProductionBox[] | null>(null);
  const [error, setError] = useState("");
  const [requirements, setRequirements] = useState<Requirement[] | null>(null);
  const [asked, setAsked] = useState(false);
  useEffect(() => {
    if (!showInputs) return;
    let live = true;
    productionApi.inputs(taskId, operation.id, onBehalfOf)
      .then((result) => { if (live) setBoxes(result.boxes); })
      .catch((reason) => { if (live) setError(reason instanceof Error ? reason.message : "Входы не загрузились"); });
    return () => { live = false; };
  }, [taskId, operation.id, onBehalfOf, showInputs]);
  useEffect(() => {
    if (!asked) return;
    let live = true;
    productionApi.operationEtk(taskId, operation.id)
      .then((result) => { if (live) setRequirements(requirementsOf(result.card, (operation.etk_ops ?? []).map((item) => item.position))); })
      .catch(() => { if (live) setRequirements([]); });
    return () => { live = false; };
  }, [asked, taskId, operation.id, operation.etk_ops]);
  return <div className="pf-block">
    {showInputs && <div className="pf-boxes"><b>Получить от предыдущей задачи:</b>{" "}
      {error ? <span className="pf-hint">{error}</span>
        : boxes === null ? <span className="pf-hint">Загрузка…</span>
        : boxes.length === 0 ? <span className="pf-hint">коробок пока нет</span>
        : boxes.map((box, index) => <span key={box.id}>{index > 0 ? "; " : ""}{box.code || box.number || "коробка"} — <b>{box.available}</b> шт</span>)}
    </div>}
    <details onToggle={(event) => { if ((event.currentTarget as HTMLDetailsElement).open) setAsked(true); }}>
      <summary>Требования</summary>
      {requirements === null ? <span className="pf-hint">Загрузка…</span>
        : requirements.length === 0 ? <span className="pf-hint">В ЭТК нет шагов контроля и типовых дефектов.</span>
        : <ul>{requirements.map((item) => <li key={item.position}><b>Оп. {String(item.position).padStart(2, "0")}</b>
          {item.controls.map((text) => <div key={text}>Контроль: {text}</div>)}
          {item.defects.length > 0 && <small>Дефекты: {item.defects.join("; ")}</small>}
        </li>)}</ul>}
    </details>
  </div>;
}
