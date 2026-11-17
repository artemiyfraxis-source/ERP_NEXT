import { useEffect, useState, type ReactNode } from "react";
import { failureText, productionApi } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { PageWindow } from "./PageWindow";
import { formatNumber } from "./format";
import { windowText } from "./productionGraph";
import { cuttingLabels, cuttingRows, cuttingState, cuttingLabelsPage, cuttingLabelsTitle, type CuttingGroup, type CuttingLabel, type CuttingMember } from "./cuttingGroups";
import type { MarkSerialRange } from "./markSerial";
import { hasMarkSerial } from "./markSerial";
import { openPrintDocument } from "./print";
import "./cuttingGroups.css";

const executionGap = "Начало, сдача и простой всей группы пока недоступны.";
const rowKey = (row: CuttingMember) => row.id;

export function CuttingGroupCard({ group, readOnly, renderInstruction }: {
  group: CuttingGroup;
  readOnly: boolean;
  renderInstruction: (row: CuttingMember, close: () => void) => ReactNode;
}) {
  const rows = cuttingRows(group);
  const [view, setView] = useState<"" | "submit" | "instruction" | "labels">("");
  const [instructionId, setInstructionId] = useState(rows[0]?.id ?? "");
  const instruction = rows.find((row) => row.id === instructionId) ?? rows[0];
  if (!rows.length) return null;
  const done = rows.every((row) => row.state === "done");
  const columns: DataColumn<CuttingMember>[] = [
    { key: "item", title: "КБ", value: (row) => row.item, render: (row) => <b>{row.item}</b> },
    { key: "name", title: "Изделие", value: (row) => row.name, grow: true, wrap: true },
    { key: "length", title: "Длина реза, мм", numeric: true, value: (row) => row.cut_length_mm == null ? null : Number(row.cut_length_mm) },
    { key: "assembly", title: "Сборок", numeric: true, value: (row) => row.assembly_quantity },
    { key: "blanks", title: "Заготовок", numeric: true, value: (row) => Number(row.blank_quantity) },
    { key: "quantity", title: "План ПФ, шт", numeric: true, value: (row) => Number(row.operation_quantity) },
    { key: "good", title: "Годные", numeric: true, value: (row) => Number(row.good) },
    { key: "defects", title: "Брак", numeric: true, value: (row) => Number(row.defects) },
    { key: "state", title: "Состояние", value: (row) => cuttingState[row.state] },
  ];
  const title = `Резка · ${group.project} · ПФ-1 Заготовки · ${rows.length} КБ`;
  return <section className={`mo-card cutting-group${done ? " done" : ""}`} aria-label={title}>
    <div className="mo-h">
      <div className="mo-name"><h2>{title}</h2><small>{group.number} · {group.site}</small></div>
      <div className="mo-side">
        {!readOnly && !done && <>
          <button type="button" className="primary" disabled title={executionGap}>Начать</button>
          <button type="button" className="secondary" aria-haspopup="dialog" onClick={() => setView("submit")}>Сдать</button>
          <button type="button" className="secondary" disabled title={executionGap}>Простой</button>
        </>}
        <button type="button" className="secondary" aria-haspopup="dialog" onClick={() => setView("instruction")}>Инструкция</button>
        <button type="button" className="secondary" aria-haspopup="dialog" onClick={() => setView("labels")}>Этикетки группы</button>
      </div>
    </div>
    <div className="mo-s mo-plan">План {windowText(group.planned_start, group.planned_finish)} · норма {formatNumber(group.norm_seconds / 60)} мин</div>
    {group.partial_view && <div className="warning-inline">Показаны только назначенные вам КБ. Этикетки — для показанных КБ.</div>}
    {!readOnly && !done && <div className="warning-inline">{executionGap}</div>}
    <DataTable rows={rows} columns={columns} rowKey={rowKey} label="КБ группы резки" compactBelow={Infinity} pageSize={1000} phoneCards={false} />
    {view === "submit" && <GroupSubmit group={group} close={() => setView("")} />}
    {view === "labels" && <GroupLabels key={`${group.id}:${group.version}`} group={group} close={() => setView("")} />}
    {view === "instruction" && instruction && <PageWindow label="Инструкция группы резки" width={1400} close={() => setView("")}>
      <label className="cutting-instruction-picker">КБ<select value={instruction.id} onChange={(event) => setInstructionId(event.target.value)}>
        {rows.map((row) => <option key={row.id} value={row.id}>{row.item} · {row.name}</option>)}
      </select></label>
      {renderInstruction(instruction, () => setView(""))}
    </PageWindow>}
  </section>;
}

function GroupSubmit({ group, close }: { group: CuttingGroup; close: () => void }) {
  const rows = cuttingRows(group);
  const [values, setValues] = useState(() => Object.fromEntries(rows.map((row) => [row.id, {
    good: String(Math.max(0, Number(row.operation_quantity) - Number(row.good) - Number(row.defects))), defects: "0",
  }])));
  const field = (row: CuttingMember, kind: "good" | "defects") => <input type="number" min="0" step="any" inputMode="decimal"
    aria-label={`${kind === "good" ? "Годные" : "Брак"} ${row.item}`} value={values[row.id][kind]}
    onChange={(event) => setValues((current) => ({ ...current, [row.id]: { ...current[row.id], [kind]: event.target.value } }))} />;
  return <PageWindow label="Сдать группу резки" width={1120} close={close}>
    <div className="panel cutting-submit">
      <h2>Сдать группу · {group.project} · {rows.length} КБ</h2>
      <div className="warning-inline" role="status">Общая сдача пока недоступна. Количества не сохранены.</div>
      <DataTable rows={rows} rowKey={rowKey} label="Сдача по КБ" compactBelow={Infinity} pageSize={1000} columns={[
        { key: "item", title: "КБ", value: (row) => row.item },
        { key: "name", title: "Изделие", value: (row) => row.name, grow: true },
        { key: "quantity", title: "План ПФ, шт", numeric: true, value: (row) => Number(row.operation_quantity) },
        { key: "good", title: "Годные, шт ПФ", numeric: true, value: (row) => values[row.id].good, render: (row) => field(row, "good") },
        { key: "defects", title: "Брак, шт ПФ", numeric: true, value: (row) => values[row.id].defects, render: (row) => field(row, "defects") },
      ]} />
      <div className="form-actions"><button type="button" className="secondary" onClick={close}>Закрыть</button>
        <button type="button" className="primary" disabled title={executionGap}>Сдать группу</button></div>
    </div>
  </PageWindow>;
}

function GroupLabels({ group, close }: { group: CuttingGroup; close: () => void }) {
  const [labels, setLabels] = useState<CuttingLabel[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    async function load() {
      const rows = cuttingRows(group);
      const ranges: Record<string, MarkSerialRange> = {};
      // ETK reads can register label issuance. Fetch only on demand, with bounded concurrency.
      const numbered = rows.filter((row) => row.labels.some((mark) => hasMarkSerial(String(mark.text ?? ""))));
      for (let i = 0; i < numbered.length; i += 4) {
        if (!live) return;
        await Promise.all(numbered.slice(i, i + 4).map(async (row) => {
          const result = await productionApi.operationEtk(row.task_id, row.operation_id);
          if (result.serial) ranges[row.id] = result.serial;
        }));
      }
      const result = cuttingLabels(group, ranges);
      if (live) setLabels(result);
    }
    void load().catch((reason) => { if (live) setError(failureText(reason, "Этикетки группы не загружены")); });
    return () => { live = false; };
  }, [group]);
  return <PageWindow label="Этикетки группы" width={1280} close={close}>
    <div className="panel cutting-labels">
      <div className="manual-stock-head"><h2>Этикетки группы · {group.project}</h2>
        <button type="button" className="primary" disabled={!labels?.length || Boolean(error)} onClick={() => labels && openPrintDocument("Этикетки группы", [cuttingLabelsPage(group, labels)])}>Печатать все</button>
        <button type="button" className="secondary" onClick={close}>Закрыть</button>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!labels && !error && <div className="empty" role="status">Загрузка…</div>}
      {labels && <>
        <p className="num" role="status">{cuttingLabelsTitle(labels)}</p>
        <DataTable rows={labels} rowKey={(row) => row.id} label="Общий список этикеток" compactBelow={Infinity} pageSize={10000} emptyText="В выданных инструкциях группы нет этикеток." columns={[
          { key: "item", title: "КБ", value: (row) => row.item },
          { key: "text", title: "Надпись", value: (row) => row.text, grow: true, wrap: true },
          { key: "material", title: "Материал", value: (row) => row.material },
          { key: "place", title: "Место", value: (row) => row.place },
          { key: "length", title: "Длина, мм", value: (row) => row.length, align: "right" },
          { key: "quantity", title: "Количество", numeric: true, value: (row) => row.quantity, render: (row) => `× ${formatNumber(row.quantity)}` },
        ]} />
      </>}
    </div>
  </PageWindow>;
}
