import { useEffect, useRef, useState } from "react";
import { failureText, productionApi, type ProductionMachineBinding, type ProductionResources } from "./api";
import "./productionMachinePicker.css";

export type MachineSelection = { key: string; ready: boolean; bindings: ProductionMachineBinding[] | undefined };

/** A choice belongs to the exact preview/operator/version, never to a model family alone. */
export function ProductionMachinePicker({ selectionKey, resources, taskId, operationId, operatorIds = [], version, onChange }: {
  selectionKey: string; resources?: ProductionResources | null;
  taskId?: string; operationId?: string; operatorIds?: string[]; version?: number;
  onChange: (value: MachineSelection) => void;
}) {
  const [loaded, setLoaded] = useState<{ key: string; data?: ProductionResources; error?: string } | null>(null);
  const [chosen, setChosen] = useState<{ key: string; values: Record<number, string> }>({ key: "", values: {} });
  const notify = useRef(onChange); notify.current = onChange;
  const operators = JSON.stringify(operatorIds);
  // Reset only when the key really changes: a late mount effect must not wipe a choice made right after render.
  useEffect(() => { setChosen((old) => old.key === selectionKey ? old : { key: selectionKey, values: {} }); }, [selectionKey]);
  useEffect(() => {
    if (resources !== undefined || !taskId || !operationId) return;
    let active = true;
    productionApi.resources(taskId, operationId, JSON.parse(operators)).then((data) => {
      if (active) setLoaded({ key: selectionKey, data });
    }).catch((reason) => { if (active) setLoaded({ key: selectionKey, error: failureText(reason, "Станки не загружены") }); });
    return () => { active = false; };
  }, [selectionKey, taskId, operationId, operators, resources]);
  const state = loaded?.key === selectionKey ? loaded : null;
  const data = resources !== undefined ? resources : state?.data;
  const values = chosen.key === selectionKey ? chosen.values : {};
  const off = data === null || data?.enabled === false;
  const requirements = data?.requirements ?? [];
  const error = state?.error || (data && data.enabled !== true && data.enabled !== false ? "Ответ о станках не распознан" : "")
    || (data?.enabled && !Array.isArray(data.requirements) ? "Требования к станкам не загружены" : "")
    || (data?.enabled && version !== undefined && data.task_version !== version ? "Задание изменилось. Обновите план и выберите станки заново." : "")
    || data?.blocked_reason || "";
  const ready = off || Boolean(data?.enabled && !error && requirements.every((row) => row.machines.some((machine) => machine.id === values[row.etk_position])));
  const bindings = ready && !off ? requirements.map((row) => ({ etk_position: row.etk_position, machine_id: values[row.etk_position] })) : undefined;
  const signature = JSON.stringify(bindings);
  useEffect(() => { notify.current({ key: selectionKey, ready, bindings }); }, [selectionKey, ready, signature]);
  if (off) return null;
  return <section className="panel production-machine-picker" aria-label="Выбор станков">
    <h3>Станки по выданной ЭТК{data?.site ? ` · ${data.site}` : ""}</h3>
    {error ? <div role="alert" className="error-banner">{error}</div> : !data ? <div role="status" className="muted">Загрузка станков…</div>
      : requirements.length === 0 ? <div className="muted">Станок не требуется</div>
        : requirements.map((row) => <label key={row.etk_position}>Операция ЭТК {row.etk_position} · {row.family}
          <select aria-label={`Станок · операция ЭТК ${row.etk_position}`} value={values[row.etk_position] ?? ""}
            onChange={(event) => setChosen({ key: selectionKey, values: { ...values, [row.etk_position]: event.target.value } })}>
            <option value="">Выберите конкретный станок</option>
            {row.machines.map((machine) => <option key={machine.id} value={machine.id}>{machine.id} · {machine.name} · {machine.model} · {machine.site}</option>)}
          </select>
          {!row.machines.length && <span role="alert" className="danger-text">Нет подходящего станка на площадке</span>}
        </label>)}
    {data?.occupancy_rule && <p className="warning-inline">{data.occupancy_rule}</p>}
  </section>;
}
