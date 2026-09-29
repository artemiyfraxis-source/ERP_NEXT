// Редактор карточки операции — общий для «Шаблонов техкарт» (запись библиотеки) и ТК проекта (операция ЭТК),
// как SUPPLYMAP PortalApp.html tcCardEditForm_: безопасность, оснастка по разделам (↑ ↓ ✕, «＋ Позиция оснастки»),
// ход операции (тип, текст, допуск; «＋ Шаг», «＋ Контроль»), типовые дефекты. Номера и пустые строки
// нормализует сервер (tcRecordNormalize_).
import { useState } from "react";
import type { OperationCardStepValue, OperationCardToolValue, OperationRecordEdit } from "./api";

export const TOOL_SECTIONS = ["Инструмент", "Матрицы", "Оснастка", "Оборудование", "Расходники"];
export const SAFETY_HEAD = /^ВАЖНО!\s*ТРЕБОВАНИЕ БЕЗОПАСНОСТИ:\s*/i;

export function TcCardEditForm({ code, initial, nameEditable = true, hint, onCancel, onSave }: {
  code: string;
  initial: OperationRecordEdit;
  nameEditable?: boolean;
  hint: string;
  onCancel: () => void;
  onSave: (draft: OperationRecordEdit) => Promise<void>;
}) {
  const [draft, setDraft] = useState<OperationRecordEdit>(() => JSON.parse(JSON.stringify(initial)) as OperationRecordEdit);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  function change(next: OperationRecordEdit) { setDraft(next); setDirty(true); }
  function setStep(index: number, patch: Partial<OperationCardStepValue>) {
    change({ ...draft, steps: draft.steps.map((step, i) => i === index ? { ...step, ...patch } : step) });
  }
  function setTool(index: number, patch: Partial<OperationCardToolValue>) {
    change({ ...draft, tooling: draft.tooling.map((tool, i) => i === index ? { ...tool, ...patch } : tool) });
  }
  function move<T>(list: T[], index: number, delta: number): T[] {
    const next = [...list];
    [next[index], next[index + delta]] = [next[index + delta], next[index]];
    return next;
  }
  function rowButtons(kind: "steps" | "tooling", index: number, count: number) {
    const list = draft[kind] as unknown[];
    return <span className="tr-rowbtn">
      <button type="button" title="Выше" aria-label="Выше" disabled={!index} onClick={() => change({ ...draft, [kind]: move(list, index, -1) })}>↑</button>
      <button type="button" title="Ниже" aria-label="Ниже" disabled={index >= count - 1} onClick={() => change({ ...draft, [kind]: move(list, index, 1) })}>↓</button>
      <button type="button" title="Удалить" aria-label="Удалить" onClick={() => change({ ...draft, [kind]: list.filter((_, i) => i !== index) })}>✕</button>
    </span>;
  }
  function cancel() {
    if (dirty && !window.confirm("Выйти без сохранения? Правки пропадут.")) return;
    onCancel();
  }
  async function save() {
    setSaving(true); setMessage("");
    try {
      await onSave(draft);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Не сохранено");
      setSaving(false);
    }
  }

  return <div className="tc-card-edit">
    <div className="form-actions">
      <button className="secondary" type="button" onClick={cancel}>Отмена</button>
      <button className="primary" type="button" disabled={saving} onClick={() => void save()}>{saving ? "Сохраняю…" : "Сохранить"}</button>
      {message && <span className="danger-text" role="alert">{message}</span>}
    </div>
    {hint && <p className="page-sub">{hint}</p>}
    <div className="tr-paper">
      <div className="tr-head">
        <div><small>Операция</small><b>{code}</b></div>
        <div className="tr-head-name"><small>Наименование</small>
          {nameEditable ? <input aria-label="Наименование операции" value={draft.name} onChange={(event) => change({ ...draft, name: event.target.value })} /> : <b>{draft.name}</b>}
        </div>
      </div>
      <label className="tr-block">Требование безопасности
        <textarea aria-label="Требование безопасности" rows={3} value={draft.safety} onChange={(event) => change({ ...draft, safety: event.target.value })} />
      </label>
      <div className="tr-block"><h4>Оснастка и расходники</h4>
        {draft.tooling.map((tool, index) => {
          const equipment = tool.section === "Оборудование";
          return <div className="tr-erow tr-tool-e" key={`tool:${index}`}>
            <select aria-label={`Раздел оснастки ${index + 1}`} value={tool.section} onChange={(event) => setTool(index, { section: event.target.value })}>
              {TOOL_SECTIONS.map((section) => <option key={section}>{section}</option>)}
            </select>
            <input aria-label={`Оснастка ${index + 1}`} placeholder="Наименование" value={tool.name} onChange={(event) => setTool(index, { name: event.target.value })} />
            <input aria-label={`Инв. № ${index + 1}`} placeholder="ОБ.xxx" value={tool.inventory_number} onChange={(event) => setTool(index, { inventory_number: event.target.value })} />
            <input aria-label={`${equipment ? "Программа" : "Параметры"} ${index + 1}`} value={equipment ? tool.program : tool.parameters}
              onChange={(event) => setTool(index, equipment ? { program: event.target.value } : { parameters: event.target.value })} />
            <input aria-label={`Норма ${index + 1}`} value={tool.norm} onChange={(event) => setTool(index, { norm: event.target.value })} />
            <input aria-label={`Ед. ${index + 1}`} value={tool.unit} onChange={(event) => setTool(index, { unit: event.target.value })} />
            {rowButtons("tooling", index, draft.tooling.length)}
          </div>;
        })}
        <div className="form-actions"><button className="secondary small" type="button" onClick={() => change({ ...draft, tooling: [...draft.tooling, { section: "Инструмент", name: "", inventory_number: "", program: "", parameters: "", norm: "", unit: "" }] })}>＋ Позиция оснастки</button></div>
      </div>
      <div className="tr-block"><h4>Ход операции</h4>
        {draft.steps.map((step, index) => <div className={`tr-erow tr-step-e${step.kind === "Контроль" ? " ctl" : ""}`} key={`step:${index}`}>
          <span className="tr-n">{index + 1}</span>
          <select aria-label={`Тип шага ${index + 1}`} value={step.kind} onChange={(event) => setStep(index, { kind: event.target.value })}>
            <option>Операция</option><option>Контроль</option>
          </select>
          <textarea aria-label={`Шаг ${index + 1}`} rows={Math.max(2, Math.ceil(step.text.length / 90))} value={step.text} onChange={(event) => setStep(index, { text: event.target.value })} />
          <textarea aria-label={`Допуск ${index + 1}`} rows={2} placeholder="—" value={step.tolerance === "-" ? "" : step.tolerance} onChange={(event) => setStep(index, { tolerance: event.target.value })} />
          {rowButtons("steps", index, draft.steps.length)}
        </div>)}
        <div className="form-actions">
          <button className="secondary small" type="button" onClick={() => change({ ...draft, steps: [...draft.steps, { kind: "Операция", text: "", tolerance: "" }] })}>＋ Шаг</button>
          <button className="secondary small" type="button" onClick={() => change({ ...draft, steps: [...draft.steps, { kind: "Контроль", text: "", tolerance: "" }] })}>＋ Контроль</button>
        </div>
      </div>
      <label className="tr-block">Типовые дефекты
        <textarea aria-label="Типовые дефекты" rows={8} value={draft.defects} onChange={(event) => change({ ...draft, defects: event.target.value })} />
      </label>
    </div>
  </div>;
}
