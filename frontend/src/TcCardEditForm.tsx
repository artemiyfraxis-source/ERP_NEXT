// Редактор карточки операции — общий для «Шаблонов техкарт» (запись библиотеки) и ТК проекта (операция ЭТК). Поведение —
// SUPPLYMAP PortalApp.html tcCardEditForm_ (безопасность, оснастка, ход операции, дефекты; «＋ Шаг», «＋ Контроль»), вид —
// карточка ЭТК: те же разделы и номера, оснастка таблицей по разделам Инструмент · Оборудование · Расходники, ход
// операции нумерованным списком с переключателем «Шаг | Контроль», дефекты списком с рисунком «Норма | Брак». Редкие
// действия строки (выше, ниже, в другой раздел, удалить) — в её меню «⋯». Номера и пустые строки нормализует сервер
// (tcRecordNormalize_); неизменённая карточка уходит на сохранение как пришла.
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode, type TextareaHTMLAttributes } from "react";
import type { OperationCardStepValue, OperationCardToolValue, OperationRecordEdit } from "./api";
import { defectGroupCode } from "./etkDefectArt";
import { DefectPicture } from "./EtkStdFigure";
import { defectsDraft, defectsStructured, serializeDefects, type DefectsEdit } from "./etkDefectEdit";
import { TOOLING_ORDER } from "./etkOperation";
import { RowMenu, type MenuItem } from "./ProcurementRowMenu";

export const TOOL_SECTIONS = ["Инструмент", "Матрицы", "Оснастка", "Оборудование", "Расходники"];
export const SAFETY_HEAD = /^ВАЖНО!\s*ТРЕБОВАНИЕ БЕЗОПАСНОСТИ:\s*/i;

/** Разделы оснастки, которые видны всегда (как в ЭТК); «Матрицы» и «Оснастка» — только если в карточке есть. */
const MAIN_SECTIONS = ["Инструмент", "Оборудование", "Расходники"];
const SECTION_TO: Record<string, string> = {
  Инструмент: "в инструмент", Оборудование: "в оборудование", Расходники: "в расходники", Матрицы: "в матрицы", Оснастка: "в оснастку",
};
type ToolField = "inventory_number" | "param" | "norm" | "unit";
/** Колонки раздела по листу шаблона (backend operation_cards.TOOL_COLUMNS): у инструмента — инв. №, у оборудования ещё
 *  программа, у расходников — параметры, норма, ед. Чужая колонка с непустым значением тоже видна, чтобы не прятать данные. */
const SECTION_FIELDS: Record<string, ToolField[]> = {
  Инструмент: ["inventory_number"], Матрицы: ["inventory_number"], Оснастка: ["inventory_number"],
  Оборудование: ["inventory_number", "param"], Расходники: ["param", "norm", "unit"],
};

/** Что считает генератор: чипы параметров шапки, вход, эскиз, результат — в форме на своих местах, без правки. */
export type EditContext = { chips?: ReactNode; input?: ReactNode; sketch?: ReactNode; result?: ReactNode };

export function TcCardEditForm({ code, initial, nameEditable = true, number, saveNote, extra, context, onCancel, onSave }: {
  code: string;
  initial: OperationRecordEdit;
  nameEditable?: boolean;
  /** Номер операции в ЭТК — кружок в шапке, как в просмотре. */
  number?: number;
  /** Короткая строка у кнопок: «Сохранение — ревизия 2». */
  saveNote?: string;
  /** Своё у кнопок сохранения (ЭТК: «Сохранить также в шаблон»). */
  extra?: ReactNode;
  /** Разделы ЭТК, которые считает генератор: показываются на своих местах без правки. */
  context?: EditContext;
  onCancel: () => void;
  onSave: (draft: OperationRecordEdit) => Promise<void>;
}) {
  const [draft, setDraft] = useState<OperationRecordEdit>(() => JSON.parse(JSON.stringify(initial)) as OperationRecordEdit);
  const [defects, setDefects] = useState<DefectsEdit | null>(() => defectsStructured(initial.defects) ? defectsDraft(initial.defects) : null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  function change(next: OperationRecordEdit) { setDraft(next); setDirty(true); }
  function changeDefects(next: DefectsEdit) {
    setDefects(next);
    change({ ...draft, defects: serializeDefects(next, initial.defects) });
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

  // Номера разделов подряд по показанным блокам — как в просмотре ЭТК.
  let shown = 0;
  const next = () => ++shown;
  return <article className="etk-op tc-edit" aria-label="Правка операции">
    <header className="etk-op-head">
      {number ? <span className="operation-number">{number}</span> : null}
      <div>
        {nameEditable
          ? <input className="tc-title" aria-label="Наименование операции" value={draft.name} onChange={(event) => change({ ...draft, name: event.target.value })} />
          : <h3>{draft.name}</h3>}
        {code && <small>{code}</small>}
      </div>
    </header>
    {context?.chips}
    <SafetyEditor value={draft.safety} onChange={(safety) => change({ ...draft, safety })} />
    {context?.input && <ReadOnlySection number={next()} title="Вход">{context.input}</ReadOnlySection>}
    <section className="etk-sec" aria-label="Инструмент, оборудование, расходники">
      <h4><span className="etk-sec-n">{next()}</span>Инструмент, оборудование, расходники</h4>
      <ToolingEditor tooling={draft.tooling} onChange={(tooling) => change({ ...draft, tooling })} />
    </section>
    <section className="etk-sec" aria-label="Ход операции">
      <h4><span className="etk-sec-n">{next()}</span>Ход операции</h4>
      <StepsEditor steps={draft.steps} onChange={(steps) => change({ ...draft, steps })} />
    </section>
    <section className="etk-sec" aria-label="Типовые дефекты">
      <h4><span className="etk-sec-n">{next()}</span>Типовые дефекты{defects ? ` · ${defects.groups.reduce((sum, group) => sum + group.items.length, 0)}` : ""}</h4>
      {defects
        ? <DefectsEditor value={defects} code={code} onChange={changeDefects} />
        : <AutoText aria-label="Типовые дефекты" className="tc-def-raw" value={draft.defects} onChange={(text) => change({ ...draft, defects: text })} />}
    </section>
    {context?.sketch && <ReadOnlySection number={next()} title="Эскиз" collapsed>{context.sketch}</ReadOnlySection>}
    {context?.result && <ReadOnlySection number={next()} title="Результат">{context.result}</ReadOnlySection>}
    <div className="tc-bar">
      {saveNote && <span className="tc-note">{saveNote}</span>}
      {extra}
      {message && <span className="error-banner tc-error" role="alert">{message}</span>}
      <button className="secondary" type="button" onClick={cancel}>Отмена</button>
      <button className="primary" type="button" disabled={saving} onClick={() => void save()}>{saving ? "Сохраняю…" : "Сохранить"}</button>
    </div>
  </article>;
}

function ReadOnlySection({ number, title, collapsed, children }: { number: number; title: string; collapsed?: boolean; children: ReactNode }) {
  const head = <h4><span className="etk-sec-n">{number}</span>{title}<small className="tc-ro-tag" title="Считает генератор по конфигурации сборки">из генератора</small></h4>;
  return collapsed
    ? <details className="etk-sec tc-ro" aria-label={title}><summary>{head}</summary>{children}</details>
    : <section className="etk-sec tc-ro" aria-label={title}>{head}{children}</section>;
}

/** Требование безопасности свёрнуто, как в ЭТК: в заголовке — начало текста, раскрыл — правишь. */
function SafetyEditor({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const preview = value.trim().split("\n")[0];
  return <details className="etk-safety tc-safety" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Требование безопасности{!open && preview && <span className="tc-preview">{preview}</span>}</summary>
    {open && <AutoText aria-label="Требование безопасности" value={value} onChange={onChange} />}
  </details>;
}

function move<T>(list: T[], from: number, to: number): T[] {
  const next = [...list];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

function ToolingEditor({ tooling, onChange }: { tooling: OperationCardToolValue[]; onChange: (tooling: OperationCardToolValue[]) => void }) {
  const [fresh, setFresh] = useState(-1);
  const rank = (section: string) => TOOLING_ORDER.includes(section) ? TOOLING_ORDER.indexOf(section) : TOOLING_ORDER.length;
  const sections = [...new Set([...MAIN_SECTIONS, ...tooling.map((tool) => tool.section)])].sort((a, b) => rank(a) - rank(b));
  const set = (index: number, patch: Partial<OperationCardToolValue>) =>
    onChange(tooling.map((tool, position) => position === index ? { ...tool, ...patch } : tool));
  const add = (section: string) => {
    setFresh(tooling.length);
    onChange([...tooling, { section, name: "", inventory_number: "", program: "", parameters: "", norm: "", unit: "" }]);
  };
  return <table className="tc-tools" aria-label="Оснастка">
    <colgroup><col className="tc-c-n" /><col /><col className="tc-c-inv" /><col className="tc-c-param" /><col className="tc-c-norm" /><col className="tc-c-unit" /><col className="tc-c-act" /></colgroup>
    <thead><tr>
      <th className="num">№</th><th>Наименование</th>
      <th title="Инвентарный номер по справочнику оборудования, например ОБ.034.2">Инв. №</th>
      <th title="У оборудования — программа станка, у расходников — параметры">Программа / параметры</th>
      <th className="num">Норма</th><th>Ед.</th><th aria-label="Действия" />
    </tr></thead>
    {sections.map((section) => {
      const rows = tooling.map((tool, index) => ({ tool, index })).filter(({ tool }) => tool.section === section);
      const targets = sections.filter((other) => other !== section && TOOL_SECTIONS.includes(other));
      return <tbody key={section} aria-label={section}>
        <tr className="tc-sec-row"><th colSpan={7} scope="rowgroup"><div>
          <span>{section}</span>{rows.length > 0 && <small>{rows.length}</small>}
          <button type="button" className="link-button" title={`Добавить строку в раздел «${section}»`} onClick={() => add(section)}>＋ Позиция</button>
        </div></th></tr>
        {rows.map(({ tool, index }, position) => {
          const fields = SECTION_FIELDS[section] ?? ["inventory_number", "param", "norm", "unit"];
          const equipment = section === "Оборудование";
          const param = equipment ? tool.program : tool.parameters;
          const visible = (field: ToolField, value: string) => fields.includes(field) || Boolean(value.trim());
          const label = `${section} ${position + 1}`;
          const items: MenuItem[] = [
            { label: "Выше", disabled: position === 0, run: () => onChange(move(tooling, index, rows[position - 1].index)) },
            { label: "Ниже", disabled: position === rows.length - 1, run: () => onChange(move(tooling, index, rows[position + 1].index)) },
            ...targets.map((target) => ({
              label: `Перенести ${SECTION_TO[target] ?? `в «${target}»`}`,
              run: () => onChange([...tooling.filter((_, other) => other !== index), { ...tool, section: target }]),
            })),
            { label: "Удалить", run: () => onChange(tooling.filter((_, other) => other !== index)) },
          ];
          return <tr key={index}>
            <td className="num tc-n">{position + 1}</td>
            <td><AutoText singleLine className="tc-tool-name" aria-label={`${label}: наименование`} autoFocus={index === fresh} value={tool.name} onChange={(name) => set(index, { name })} /></td>
            <td>{visible("inventory_number", tool.inventory_number) && <input className="tc-cell" aria-label={`${label}: инв. №`} title="Например ОБ.034.2" value={tool.inventory_number} onChange={(event) => set(index, { inventory_number: event.target.value })} />}</td>
            <td>{visible("param", param) && <input className="tc-cell" aria-label={`${label}: ${equipment ? "программа" : "параметры"}`} value={param}
              onChange={(event) => set(index, equipment ? { program: event.target.value } : { parameters: event.target.value })} />}</td>
            <td>{visible("norm", tool.norm) && <input className="tc-cell num" aria-label={`${label}: норма`} value={tool.norm} onChange={(event) => set(index, { norm: event.target.value })} />}</td>
            <td>{visible("unit", tool.unit) && <input className="tc-cell" aria-label={`${label}: ед.`} value={tool.unit} onChange={(event) => set(index, { unit: event.target.value })} />}</td>
            <td className="tc-act"><RowMenu label={`Действия: ${label}`} items={items} /></td>
          </tr>;
        })}
      </tbody>;
    })}
  </table>;
}

function StepsEditor({ steps, onChange }: { steps: OperationCardStepValue[]; onChange: (steps: OperationCardStepValue[]) => void }) {
  const [fresh, setFresh] = useState(-1);
  const set = (index: number, patch: Partial<OperationCardStepValue>) =>
    onChange(steps.map((step, position) => position === index ? { ...step, ...patch } : step));
  const add = (kind: string) => { setFresh(steps.length); onChange([...steps, { kind, text: "", tolerance: "" }]); };
  return <>
    <div className="tc-steps">
      <div className="tc-steps-head" aria-hidden="true"><span>№</span><span>Тип</span><span>Содержание</span><span>Допуск</span><span /></div>
      <ol aria-label="Шаги">{steps.map((step, index) => {
        const control = step.kind === "Контроль";
        const kind = (value: string) => { if (step.kind !== value) set(index, { kind: value }); };
        return <li key={index} className={`tc-step${control ? " ctl" : ""}`}>
          <span className="tc-n">{index + 1}</span>
          <span className="tc-kind" role="group" aria-label={`Тип шага ${index + 1}`}>
            <button type="button" aria-pressed={!control} onClick={() => kind("Операция")}>Шаг</button>
            <button type="button" aria-pressed={control} onClick={() => kind("Контроль")}>Контроль</button>
          </span>
          <AutoText aria-label={`Шаг ${index + 1}`} autoFocus={index === fresh} value={step.text} onChange={(text) => set(index, { text })} />
          <AutoText aria-label={`Допуск ${index + 1}`} className="tc-tol" value={step.tolerance === "-" ? "" : step.tolerance} onChange={(tolerance) => set(index, { tolerance })} />
          <RowMenu label={`Действия: шаг ${index + 1}`} items={[
            { label: "Выше", disabled: index === 0, run: () => onChange(move(steps, index, index - 1)) },
            { label: "Ниже", disabled: index === steps.length - 1, run: () => onChange(move(steps, index, index + 1)) },
            { label: "Удалить", run: () => onChange(steps.filter((_, position) => position !== index)) },
          ]} />
        </li>;
      })}</ol>
    </div>
    <div className="tc-add">
      <button className="secondary small" type="button" onClick={() => add("Операция")}>＋ Шаг</button>
      <button className="secondary small" type="button" onClick={() => add("Контроль")}>＋ Контроль</button>
    </div>
  </>;
}

/** Дефекты списком по группам: рисунок «Норма | Брак» (выбирается по названию, как в ЭТК), название, признак/причина. */
function DefectsEditor({ value, code, onChange }: { value: DefectsEdit; code: string; onChange: (value: DefectsEdit) => void }) {
  const [fresh, setFresh] = useState("");
  const setGroup = (at: number, patch: Partial<DefectsEdit["groups"][number]>) =>
    onChange({ ...value, groups: value.groups.map((group, position) => position === at ? { ...group, ...patch } : group) });
  return <div className="tc-defects">
    {value.intro.length > 0 && <small className="etk-def-intro">{value.intro.join(" · ")}</small>}
    {value.groups.length > 0 && <div className="tc-def-cols" aria-hidden="true"><span>Норма | брак</span><span>Дефект</span><span>Признак, причина</span><span /></div>}
    {value.groups.map((group, at) => <div className="tc-def-group" key={at} role="group" aria-label={group.title || "Дефекты без группы"}>
      {(group.title || group.origin === null || at > 0) && <div className="tc-def-head">
        <AutoText singleLine className="tc-def-title" aria-label={`Группа ${at + 1}`} placeholder="Название группы" autoFocus={fresh === `${at}`}
          value={group.title} onChange={(title) => setGroup(at, { title })} />
        <RowMenu label={`Действия: группа ${at + 1}`} items={[
          { label: "Удалить группу", run: () => onChange({ ...value, groups: value.groups.filter((_, position) => position !== at) }) },
        ]} />
      </div>}
      {group.items.map((item, index) => {
        const label = `${at + 1}.${index + 1}`;
        const setItem = (patch: Partial<typeof item>) => setGroup(at, { items: group.items.map((other, position) => position === index ? { ...other, ...patch } : other) });
        return <div className="tc-def" key={index}>
          <DefectPicture className="tc-def-pic" name={item.name} note={item.text} code={defectGroupCode(code, group.title)} />
          <AutoText singleLine className="tc-def-name" aria-label={`Дефект ${label}`} autoFocus={fresh === `${at}:${index}`} value={item.name} onChange={(name) => setItem({ name })} />
          <AutoText aria-label={`Признак ${label}`} value={item.text} onChange={(text) => setItem({ text })} />
          <RowMenu label={`Действия: дефект ${label}`} items={[
            { label: "Выше", disabled: index === 0, run: () => setGroup(at, { items: move(group.items, index, index - 1) }) },
            { label: "Ниже", disabled: index === group.items.length - 1, run: () => setGroup(at, { items: move(group.items, index, index + 1) }) },
            { label: "Удалить", run: () => setGroup(at, { items: group.items.filter((_, position) => position !== index) }) },
          ]} />
        </div>;
      })}
      <button type="button" className="link-button tc-add-link" onClick={() => {
        setFresh(`${at}:${group.items.length}`);
        setGroup(at, { items: [...group.items, { name: "", text: "" }] });
      }}>＋ Дефект</button>
    </div>)}
    <div className="tc-add">
      <button className="secondary small" type="button" onClick={() => {
        setFresh(`${value.groups.length}`);
        onChange({ ...value, groups: [...value.groups, { title: "", items: [{ name: "", text: "" }], origin: null }] });
      }}>＋ Группа</button>
    </div>
  </div>;
}

/** Поле, которое растёт по тексту (без ручки растягивания). `singleLine` — Enter не переносит строку (названия). */
function AutoText({ value, onChange, singleLine, className = "", ...rest }: Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "value" | "onChange"> & {
  value: string; onChange: (value: string) => void; singleLine?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fit = () => {
    const element = ref.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight + element.offsetHeight - element.clientHeight}px`;
  };
  useLayoutEffect(fit, [value]);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    let width = element.clientWidth;
    const observer = new ResizeObserver(() => { if (element.clientWidth !== width) { width = element.clientWidth; fit(); } });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const keyDown = singleLine ? (event: KeyboardEvent<HTMLTextAreaElement>) => { if (event.key === "Enter") event.preventDefault(); } : undefined;
  return <textarea ref={ref} rows={1} className={`tc-auto ${className}`.trim()} value={value} onKeyDown={keyDown}
    onChange={(event) => onChange(singleLine ? event.target.value.replace(/\s*\n\s*/g, " ") : event.target.value)} {...rest} />;
}
