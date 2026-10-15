import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { api, failureText, type Principal } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { hasAccess } from "./navigation";
import { PageWindow } from "./PageWindow";
import { fieldTitle } from "./referenceLabels";

/* «Скачать для правки» / «Загрузить исправленную» (владелец 14.10: «во всех базах данных должна быть возможность скачать
   таблицу и вставить исправленную»). Файл .xlsx собирает сервер (references/roundtrip.py): «ID записи», все поля вкладки
   под подписями этого экрана, скрытая «Версия». Загрузка сначала проверяется — окно показывает, что изменится, и только
   «Применить» записывает; строка, которую после скачивания поменяли, пропускается, удалить запись файлом нельзя. */

/** Вкладка «Базы данных» → таблица сервера и её список (кабели по видам, оболочки из материалов). */
const TARGETS: Record<string, { table: string; kind?: string }> = {
  templates: { table: "op" },
  ob: { table: "ob" },
  coax: { table: "coax" },
  ter: { table: "ter" },
  cab_coax: { table: "prov", kind: "coax" },
  cab_wire: { table: "prov", kind: "wire" },
  cab_multi: { table: "prov", kind: "multi" },
  cab_other: { table: "prov", kind: "other" },
  cov: { table: "mat", kind: "cov" },
  mat: { table: "mat" },
  // «Материалы» расформированы (15.10.2026): вкладки по назначению выгружают всю БД.МАТ — файл тот же, строки по ID.
  mat_fix: { table: "mat" },
  mat_seal: { table: "mat" },
  mat_sleeve: { table: "mat" },
  mat_use: { table: "mat" },
  suppliers: { table: "suppliers" },
  countries: { table: "countries" },
  gauges: { table: "wire-gauges" },
};
// Карты «Домен → поставщик» / «Изготовитель → страна» правит закупщик (как на экране и в API).
const BUYER_TABLES = ["suppliers", "countries"];

export interface RoundtripChange { field: string; title: string; old: string; new: string }
export interface RoundtripReport {
  tab: string;
  label: string;
  dry_run: boolean;
  applied: boolean;
  file_name: string;
  columns: Array<{ field: string; title: string }>;
  ignored_columns: string[];
  updated: Array<{ row: number; id: string; title: string; changes: RoundtripChange[] }>;
  created: Array<{ row: number; title: string; fields: Array<{ field: string; title: string; value: string }> }>;
  conflicts: Array<{ row: number; id: string; title: string; reason: string }>;
  not_found: Array<{ row: number; id: string; reason: string }>;
  errors: Array<{ row: number; id: string; title: string; reason: string }>;
  unchanged: number;
}

export const roundtripApi = {
  fields: (table: string, kind = "") =>
    api<{ fields: string[]; titles: Record<string, string>; label: string; rows: number }>(
      `/api/references/import/${table}/file/fields?${new URLSearchParams({ kind })}`),
  exportFile: (table: string, kind: string, titles: Record<string, string>) =>
    api<{ file_name: string; content_type: string; content_base64: string; rows: number }>(
      `/api/references/import/${table}/file/export`, { method: "POST", body: JSON.stringify({ titles, kind }), safe: true }),
  upload: (table: string, fileName: string, contentBase64: string, dryRun: boolean) =>
    api<RoundtripReport>(`/api/references/import/${table}/file?dry_run=${dryRun ? 1 : 0}`, {
      method: "POST", body: JSON.stringify({ file_name: fileName, content_base64: contentBase64 }), safe: dryRun,
    }),
};

function saveFile(name: string, type: string, base64: string) {
  const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

/** Кнопки вкладки: «Скачать для правки» — всем, кто видит базу; «Загрузить исправленную» — тем, кто её правит. */
export function ReferenceRoundtrip({ tab, principal, onApplied }: {
  tab: string; principal: Principal; onApplied: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [upload, setUpload] = useState<{ name: string; content: string } | null>(null);
  const target = TARGETS[tab];
  if (!target || !hasAccess(principal.permissions, "erpref")) return null;
  const { table, kind = "" } = target;
  const canLoad = principal.permissions.erpref === "edit"
    && (!BUYER_TABLES.includes(table) || principal.permissions.procurement === "edit");

  async function download() {
    setBusy(true); setError("");
    try {
      const { fields, titles } = await roundtripApi.fields(table, kind);
      const shown = Object.fromEntries(fields.map((field) => [field, titles[field] ?? fieldTitle(field)]));
      const file = await roundtripApi.exportFile(table, kind, shown);
      saveFile(file.file_name, file.content_type, file.content_base64);
    } catch (reason) {
      setError(failureText(reason, "Таблица не скачана"));
    } finally {
      setBusy(false);
    }
  }

  async function pick(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError("");
    try {
      setUpload({ name: file.name, content: await fileBase64(file) });
    } catch (reason) {
      setError(failureText(reason, "Не удалось прочитать файл"));
    }
  }

  return <>
    <button type="button" className="secondary small" disabled={busy} onClick={() => void download()}
      title="Таблица .xlsx: все поля вкладки и «ID записи» — поправьте и загрузите обратно">{busy ? "Готовлю файл…" : "Скачать для правки"}</button>
    {canLoad && <>
      <button type="button" className="secondary small" onClick={() => input.current?.click()}>Загрузить исправленную</button>
      <input ref={input} type="file" hidden aria-label="Исправленная таблица .xlsx" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => void pick(event)} />
    </>}
    {error && <span className="tone-danger" role="alert">{error}</span>}
    {upload && <RoundtripWindow table={table} fileName={upload.name} content={upload.content}
      close={() => setUpload(null)} onApplied={onApplied} />}
  </>;
}

type View = "changes" | "created" | "skipped";
type ChangeRow = RoundtripChange & { key: string; row: number; record: string };
type CreatedRow = { key: string; row: number; title: string; values: string };
type SkippedRow = { key: string; row: number; record: string; what: string; tone: string; reason: string };

const plural = (count: number, one: string, few: string, many: string) => {
  const tens = count % 100;
  const units = count % 10;
  return tens >= 11 && tens <= 14 ? many : units === 1 ? one : units >= 2 && units <= 4 ? few : many;
};

/** Окно проверки файла: что изменится (поле, было → станет), новые строки, пропущенные с причиной; «Применить». */
export function RoundtripWindow({ table, fileName, content, close, onApplied }: {
  table: string; fileName: string; content: string; close: () => void; onApplied: () => void;
}) {
  const [report, setReport] = useState<RoundtripReport | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [view, setView] = useState<View | null>(null);

  async function send(dryRun: boolean) {
    setBusy(true); setError("");
    try {
      const result = await roundtripApi.upload(table, fileName, content, dryRun);
      setReport(result);
      if (!dryRun) onApplied();
    } catch (reason) {
      setError(failureText(reason, dryRun ? "Файл не проверен" : "Изменения не записаны"));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => { void send(true); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const changes: ChangeRow[] = (report?.updated ?? []).flatMap((item) => item.changes.map((change) => ({
    ...change, key: `${item.row}:${change.field}`, row: item.row, record: item.title,
  })));
  const created: CreatedRow[] = (report?.created ?? []).map((item) => ({
    key: String(item.row), row: item.row, title: item.title,
    values: item.fields.map((value) => `${value.title}: ${value.value}`).join(" · "),
  }));
  const skipped: SkippedRow[] = [
    ...(report?.conflicts ?? []).map((item) => ({ key: `c${item.row}`, row: item.row, record: item.title, what: "Изменена после скачивания", tone: "tone-warn", reason: item.reason })),
    ...(report?.not_found ?? []).map((item) => ({ key: `n${item.row}`, row: item.row, record: item.id, what: "Нет записи", tone: "tone-warn", reason: item.reason })),
    ...(report?.errors ?? []).map((item) => ({ key: `e${item.row}`, row: item.row, record: item.title || item.id, what: "Ошибка", tone: "tone-danger", reason: item.reason })),
  ].sort((a, b) => a.row - b.row);
  const records = report?.updated.length ?? 0;
  const fresh = report?.created.length ?? 0;
  const shown: View = view ?? (changes.length ? "changes" : created.length ? "created" : "skipped");
  const applied = Boolean(report?.applied);

  const changeColumns: DataColumn<ChangeRow>[] = [
    { key: "row", title: "Строка", numeric: true, value: (row) => row.row },
    { key: "record", title: "Запись", value: (row) => row.record, render: (row) => <strong>{row.record}</strong> },
    { key: "title", title: "Поле", value: (row) => row.title },
    { key: "old", title: "Было", value: (row) => row.old, render: (row) => row.old || <span className="muted">пусто</span> },
    { key: "new", title: "Станет", value: (row) => row.new, render: (row) => row.new ? <strong>{row.new}</strong> : <span className="tone-warn">стереть</span> },
  ];
  const createdColumns: DataColumn<CreatedRow>[] = [
    { key: "row", title: "Строка", numeric: true, value: (row) => row.row },
    { key: "title", title: "Запись", value: (row) => row.title, render: (row) => <strong>{row.title}</strong> },
    { key: "values", title: "Поля", value: (row) => row.values, render: (row) => <span className="db-clip" title={row.values}>{row.values}</span> },
  ];
  const skippedColumns: DataColumn<SkippedRow>[] = [
    { key: "row", title: "Строка", numeric: true, value: (row) => row.row },
    { key: "record", title: "Запись", value: (row) => row.record },
    { key: "what", title: "Почему пропущена", value: (row) => row.what, render: (row) => <span className={row.tone}>{row.what}</span> },
    { key: "reason", title: "Подробно", value: (row) => row.reason },
  ];

  return <PageWindow label={`Загрузка исправленной таблицы: ${report?.label ?? fileName}`} close={close} width={1280}>
    <section className="panel roundtrip-window">
      <div className="manual-stock-head">
        <h2>{applied ? "Записано из файла" : "Проверка файла"}{report ? ` · ${report.label}` : ""}</h2>
        <button type="button" className="secondary small" onClick={close}>Закрыть</button>
      </div>
      <p className="page-sub">Файл: {fileName}</p>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {busy && !report && <div className="empty">Проверяю файл…</div>}
      {report && <>
        {applied && <div className="success-banner" role="status">
          Записано: изменено {records} {plural(records, "запись", "записи", "записей")}, добавлено {fresh}.
          {skipped.length > 0 && ` Пропущено строк: ${skipped.length}.`}</div>}
        <div className="chip-row" aria-label="Итог проверки">
          <span className="chip-item"><b className="tnum">{records}</b> {applied ? "изменено" : "изменится"} ({changes.length} {plural(changes.length, "поле", "поля", "полей")})</span>
          <span className="chip-item"><b className="tnum">{fresh}</b> {applied ? "добавлено" : "новых"}</span>
          <span className="chip-item"><b className="tnum">{report.unchanged}</b> без изменений</span>
          {report.conflicts.length > 0 && <span className="chip-item tone-warn"><b className="tnum">{report.conflicts.length}</b> изменены после скачивания</span>}
          {report.not_found.length > 0 && <span className="chip-item tone-warn"><b className="tnum">{report.not_found.length}</b> нет записи</span>}
          {report.errors.length > 0 && <span className="chip-item tone-danger"><b className="tnum">{report.errors.length}</b> с ошибкой</span>}
        </div>
        <p className="page-sub">Строки, которых нет в файле, не трогаются — удалить запись файлом нельзя. Строка, изменённая после скачивания, пропускается.</p>
        {report.ignored_columns.length > 0 && <p className="warning-inline">Колонки не узнаны и не загружаются: {report.ignored_columns.join(", ")}</p>}
        <div className="seg-tabs" role="tablist" aria-label="Списки проверки">
          {([["changes", "Изменения", changes.length], ["created", "Новые записи", created.length], ["skipped", "Пропущены", skipped.length]] as Array<[View, string, number]>)
            .map(([id, label, count]) => <button key={id} type="button" role="tab" aria-selected={shown === id} className={shown === id ? "on" : ""} onClick={() => setView(id)}>
              {label}<span className="ship-tab-count">{count}</span></button>)}
        </div>
        {shown === "changes" && <DataTable label="Изменения" rows={changes} columns={changeColumns} rowKey={(row) => row.key} compactBelow={12} pageSize={200}
          emptyText="Полей с другим значением нет." />}
        {shown === "created" && <DataTable label="Новые записи" rows={created} columns={createdColumns} rowKey={(row) => row.key} compactBelow={12} pageSize={200}
          emptyText="Строк без «ID записи» нет — новых записей не будет." />}
        {shown === "skipped" && <DataTable label="Пропущены" rows={skipped} columns={skippedColumns} rowKey={(row) => row.key} compactBelow={12} pageSize={200}
          emptyText="Пропущенных строк нет." />}
        <div className="form-actions">
          {!applied && <button type="button" className="primary" disabled={busy || records + fresh === 0} onClick={() => void send(false)}>
            {busy ? "Записываю…" : records + fresh === 0 ? "Записывать нечего" : `Применить: ${records} ${plural(records, "изменение", "изменения", "изменений")}, ${fresh} ${plural(fresh, "новая", "новые", "новых")}`}
          </button>}
          <button type="button" className="secondary" onClick={close}>{applied ? "Закрыть" : "Отмена"}</button>
        </div>
      </>}
    </section>
  </PageWindow>;
}
