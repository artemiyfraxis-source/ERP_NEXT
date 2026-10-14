// Карточка операции в «Базе данных»: читается как лист ЭТК — шапка (код, название, нормы), параметры операции по
// группам (строка БД.ОП), оснастка, ход операции, типовые дефекты. Один «Править»: шаблон листа (TcCardEditForm — шаги,
// оснастка, дефекты) и параметры строки БД.ОП, каждая часть по своему праву (erptctpl / erpref).
import { useMemo, useState, type ReactNode } from "react";
import { referencesApi, type OperationCardDetail, type OperationRecordEdit, type TechOperationRecord } from "./api";
import { changedFields, groupOpFields, hasValue, normBasis, timeUnit, typeForBasis, type OpField, type OpGroup } from "./opFields";
import { DefectsView, ToolingBySection, editDraft } from "./ProjectEtk";
import { SAFETY_HEAD, TcCardEditForm } from "./TcCardEditForm";

type Fields = Record<string, string>;

/** Время в шапке: «3,50 с на метр»; пусто — «—». */
function seconds(value: string | undefined, type: string, own = false) {
  const text = (value ?? "").trim();
  return text ? `${text} ${own ? "с" : timeUnit(type)}` : "—";
}

function ParamRow({ field, draft, onChange }: { field: OpField; draft?: Fields; onChange?: (key: string, value: string) => void }) {
  const editing = Boolean(draft && onChange);
  const value = draft ? (draft[field.key] ?? "") : field.value;
  if (!editing) {
    const shown = field.basis
      ? (normBasis(value) === "meter" ? "на метр" : normBasis(value) === "operation" ? "на операцию" : "")
      : value;
    return <div className="op-row"><dt>{field.label}</dt><dd>{hasValue(shown) ? <>{shown}{field.unit && !field.basis ? <small> {field.unit}</small> : null}</> : <span className="cell-empty">—</span>}</dd></div>;
  }
  const label = `${field.label}`;
  if (field.readOnly) return <div className="op-row"><dt>{field.label}</dt><dd><b>{value}</b></dd></div>;
  return <div className="op-row">
    <dt><label htmlFor={`op-${field.key}`}>{field.label}</label></dt>
    <dd>{field.basis
      ? <select id={`op-${field.key}`} aria-label={label} value={normBasis(value)} onChange={(event) => onChange!(field.key, typeForBasis(value, event.target.value as "meter" | "operation" | ""))}>
        <option value="">не задано</option><option value="operation">на операцию</option><option value="meter">на метр</option>
      </select>
      : <><input id={`op-${field.key}`} aria-label={label} value={value} onChange={(event) => onChange!(field.key, event.target.value)} />{field.unit && <small>{field.unit}</small>}</>}</dd>
  </div>;
}

function ParamGroup({ group, draft, onChange }: { group: OpGroup; draft?: Fields; onChange?: (key: string, value: string) => void }) {
  const main = ["ident", "equip", "norms"].includes(group.id);
  const rows = draft ? group.fields : group.fields.filter((field) => main || hasValue(field.value));
  if (!rows.length) return null;
  return <div className="op-group"><h5>{group.title}</h5><dl>{rows.map((field) => <ParamRow key={field.key} field={field} draft={draft} onChange={onChange} />)}</dl></div>;
}

/** «Параметры операции»: строка БД.ОП по группам; `draft` — режим правки. Исходные данные расчёта — свёрнуты. */
function OpParams({ fields, draft, onChange, hideName }: { fields: Fields; draft?: Fields; onChange?: (key: string, value: string) => void; hideName?: boolean }) {
  const groups = useMemo(() => {
    const all = groupOpFields(draft ?? fields);
    // В правке с шаблоном название правится в заголовке формы — второе поле «Название» только сбивало бы.
    return hideName ? all.map((group) => ({ ...group, fields: group.fields.filter((field) => field.label !== "Название") })) : all;
  }, [fields, draft, hideName]);
  const main = groups.filter((group) => group.id !== "raw");
  const raw = groups.find((group) => group.id === "raw");
  const rawCount = raw?.fields.filter((field) => hasValue(field.value)).length ?? 0;
  return <section className="tr-block op-params" aria-label="Параметры операции">
    <h4>Параметры операции</h4>
    <div className="op-groups">{main.map((group) => <ParamGroup key={group.id} group={group} draft={draft} onChange={onChange} />)}</div>
    {raw && (draft || rawCount > 0) && <details className="op-raw">
      <summary>{raw.title}<span>{rawCount}</span></summary>
      <div className="op-groups"><ParamGroup group={{ ...raw, title: "" }} draft={draft} onChange={onChange} /></div>
    </details>}
  </section>;
}

function Steps({ template }: { template: OperationCardDetail }) {
  const steps = template.document.steps;
  const tolerance = (value?: string) => (value && value !== "-" ? value : "");
  const withTolerance = steps.some((step) => tolerance(step.tolerance));
  return <section className="tr-block" aria-label="Ход операции"><h4>Ход операции <span>{steps.length}</span></h4>
    {steps.length ? <table className="tr-steps"><thead><tr><th>№</th><th>Содержание</th>{withTolerance && <th>Допуск</th>}</tr></thead><tbody>
      {steps.map((step, index) => <tr key={`${step.number}:${index}`} className={step.kind === "Контроль" ? "ctl" : ""}>
        <td>{step.number}</td>
        <td>{step.kind === "Контроль" && <span className="op-ctl-chip">Контроль</span>}<div className="pre-line">{step.text}</div></td>
        {withTolerance && <td>{tolerance(step.tolerance) || "—"}</td>}
      </tr>)}
    </tbody></table> : <div className="empty">Шагов нет.</div>}
  </section>;
}

export function OperationCard({ code, name, template, op, canEditTemplate, canEditOp, onBack, onSaveTemplate, onOpSaved }: {
  code: string;
  name: string;
  /** Лист БД.ТК; у операции только из БД.ОП его нет. */
  template?: OperationCardDetail;
  op?: TechOperationRecord;
  canEditTemplate: boolean;
  canEditOp: boolean;
  onBack: () => void;
  onSaveTemplate: (draft: OperationRecordEdit) => Promise<void>;
  onOpSaved: (record: TechOperationRecord) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Fields>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const templateEditable = Boolean(template) && canEditTemplate;
  const opEditable = Boolean(op) && canEditOp;
  const fields = op?.fields ?? {};
  const type = fields["Тип операции"] ?? "";
  const norms = template?.norms;

  function startEdit() { setDraft({ ...fields }); setError(""); setEditing(true); }
  const change = (key: string, value: string) => setDraft((current) => ({ ...current, [key]: value }));

  /** Сохраняет только изменённые поля строки БД.ОП; возвращает, было ли что менять. */
  async function saveOp(): Promise<void> {
    if (!op || !opEditable) return;
    const edits = changedFields(op.fields, draft);
    if (edits.length) onOpSaved(await referencesApi.editRecord(op.id, edits));
  }

  async function saveBoth(templateDraft: OperationRecordEdit, initial: OperationRecordEdit) {
    await saveOp();
    if (JSON.stringify(templateDraft) !== JSON.stringify(initial)) await onSaveTemplate(templateDraft);
    setEditing(false);
  }

  async function saveParamsOnly() {
    setBusy(true); setError("");
    try { await saveOp(); setEditing(false); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Параметры операции не сохранены"); }
    finally { setBusy(false); }
  }

  const params = (edit: boolean, hideName = false): ReactNode => op ? <OpParams fields={fields} draft={edit && opEditable ? draft : undefined} onChange={change} hideName={hideName} /> : null;

  if (editing && template && templateEditable) {
    const initial = editDraft(template.name, template.document);
    return <TcCardEditForm
      code={template.code}
      initial={initial}
      saveNote="Сохранение — в библиотеку, в ТК проектов при следующей сборке"
      context={{ chips: params(true, true) }}
      onCancel={() => setEditing(false)}
      onSave={(templateDraft) => saveBoth(templateDraft, initial)}
    />;
  }

  const head = (actions?: ReactNode) => <div className={`tr-head${actions ? " with-act" : ""}`}>
    <div><small>Операция</small><b className="tr-code">{code}</b></div>
    <div className="tr-head-name"><small>Наименование</small><b>{template?.name || name || "—"}</b></div>
    <div><small>Время операции</small><b>{template
      ? (template.norms_known === false ? "справочник не загружен" : norms ? seconds(norms.operation_seconds || fields.tOp, type) : <span className="danger-text">нет в БД.ОП</span>)
      : seconds(fields.tOp, type)}</b></div>
    <div><small>Подготовка</small><b>{seconds(norms?.setup_seconds || fields.tPrep, type, true)}</b></div>
    {actions && <div className="tr-head-act">{actions}</div>}
  </div>;

  if (editing) {
    // Шаблона нет или права на него нет — правятся только параметры строки БД.ОП.
    return <article className="panel template-detail tr-paper" aria-label="Правка параметров операции">
      {head()}
      {params(true)}
      <div className="tc-bar op-bar">
        {error && <span className="error-banner tc-error" role="alert">{error}</span>}
        <button className="secondary" type="button" onClick={() => setEditing(false)}>Отмена</button>
        <button className="primary" type="button" disabled={busy} onClick={() => void saveParamsOnly()}>{busy ? "Сохраняю…" : "Сохранить"}</button>
      </div>
    </article>;
  }

  return <article className="panel template-detail tr-paper">
    {head(<><button className="secondary" onClick={onBack}>← Все операции</button>{(templateEditable || opEditable) && <button className="primary" onClick={startEdit}>Править</button>}</>)}
    {!template && <div className="warning-inline">Шаблона листа (БД.ТК) у этой операции нет: шагов, оснастки и контроля нет.</div>}
    {template?.document.safety && <div className="warning-inline"><strong>Требование безопасности</strong><br />{template.document.safety.replace(SAFETY_HEAD, "")}</div>}
    {params(false)}
    {template && <>
      <section className="tr-block" aria-label="Оснастка и расходники"><h4>Оснастка и расходники</h4><ToolingBySection tooling={template.document.tooling} /></section>
      <Steps template={template} />
      {template.document.defects && <DefectsView text={template.document.defects} code={template.code} alwaysOpen />}
      {template.changed_at && <div className="tr-foot">{`Изменено ${new Date(template.changed_at).toLocaleString("ru-RU")}${template.changed_by ? ` · ${template.changed_by}` : ""}`}</div>}
    </>}
  </article>;
}
