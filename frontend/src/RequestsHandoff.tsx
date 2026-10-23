import { useEffect, useRef, useState } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { failureText, isAborted, latestRead, requestsApi, type RequestRow } from "./api";
import { countLabel, formatDate } from "./format";
import { projectLabel } from "./internalProjects";
import { projectFromDesignation } from "./projectNumber";
import "./procurementNav.css";

/* «Не переданы в закупку» над «Заявками» в «Снабжении» (правки Вити 2–4, владелец 23.10): раздел «Заявки» ушёл из меню,
   а передать заявку в закупку по-прежнему может тот, у кого право «Заявки» на правке (коммерческий отдел, руководитель
   проектов), даже если «Снабжение» открыто ему только на просмотр. Выбрать строки → «В закупку», как в реестре. */

const HANDOFF_LIMIT = 100; // как в реестре «Заявки»: столько заявок за один раз

const columns: DataColumn<RequestRow>[] = [
  { key: "legacy_id", title: "ID", value: (row) => row.legacy_id, render: (row) => <strong>{row.legacy_id}</strong> },
  { key: "name", title: "Наименование", value: (row) => row.name, render: (row) => <>{row.name}<small>{row.item_type}</small></>, wrap: true },
  { key: "project", title: "Проект", value: (row) => projectLabel(row.project_reference || projectFromDesignation(row.designation)), filter: true },
  { key: "quantity", title: "Кол-во", value: (row) => `${row.quantity} ${row.unit}`, numeric: true },
  { key: "initiator", title: "Инициатор", value: (row) => row.initiator, filter: true },
  { key: "created_at", title: "Дата запроса", value: (row) => formatDate(row.created_at) },
  { key: "planned_date", title: "План. дата", value: (row) => formatDate(row.planned_date) },
];

export function RequestsHandoff({ canHandoff, openRegister }: { canHandoff: boolean; openRegister?: () => void }) {
  const read = useRef(latestRead()).current;
  const [rows, setRows] = useState<RequestRow[] | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    try {
      const result = await requestsApi.list("", read());
      setRows(result.requests.filter((row) => row.status === "Новый"));
      setError("");
    } catch (reason) {
      if (isAborted(reason)) return;
      setError(failureText(reason, "Заявки не загрузились"));
    }
  }

  useEffect(() => { if (canHandoff) void load(); }, [canHandoff]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handoff(selected: RequestRow[]) {
    setError("");
    setNotice("");
    try {
      await requestsApi.handoff(selected.map((row) => row.id));
      setNotice(selected.length === 1
        ? `Заявка ${selected[0].legacy_id} передана в закупку`
        : `Передано в закупку: ${countLabel(selected.length, ["заявка", "заявки", "заявок"])}`);
    } catch (reason) {
      setError(failureText(reason, "Заявки не переданы в закупку"));
      throw reason;
    }
    await load();
  }

  const register = openRegister && <button type="button" className="link-button" onClick={openRegister}>Реестр заявок →</button>;
  if (!canHandoff) return register ? <div className="handoff-strip handoff-strip-line">{register}</div> : null;
  return (
    <section className="panel handoff-strip" aria-label="Не переданы в закупку">
      <div className="handoff-head">
        <h2>Не переданы в закупку {rows && <span className="page-count">{rows.length}</span>}</h2>
        {register}
      </div>
      {error && <div className="error-banner" role="alert">{error} <button type="button" className="secondary small" onClick={() => void load()}>Повторить</button></div>}
      {notice && <div className="success-banner" role="status">{notice}</div>}
      {!rows && !error && <div className="empty">Загрузка…</div>}
      {rows && (rows.length === 0
        ? <p className="page-sub handoff-empty">Все заявки переданы в закупку.</p>
        : <DataTable
          rows={rows}
          columns={columns}
          rowKey={(row) => row.id}
          rowLabel={(row) => row.legacy_id}
          storageKey="procurement-handoff"
          label="Не переданы в закупку"
          compactBelow={8}
          pageSize={20}
          bulkActions={[{
            label: "В закупку",
            disabled: (selected) => selected.length > HANDOFF_LIMIT,
            run: (selected) => handoff(selected),
          }]}
        />)}
    </section>
  );
}
