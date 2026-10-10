import { useCallback, useEffect, useState } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import {
  documentsApi,
  failureText,
  type DocumentDraft,
  type DocumentKind,
  type DocumentScope,
  type DocumentSheet,
  type DocumentsOverview,
  type JournalDocument,
  type OwnDocument,
} from "./api";
import { formatDate } from "./format";

/** Вкладка «Документы» в «Моей карточке»: что сотрудник обязан прочитать (PDF, подпись «Ознакомлен» PIN или паролем),
 *  его история ознакомления и — с правом «Кадры» — управление: создать документ, новая редакция, архив, лист
 *  ознакомления по редакциям. Ошибки сервера (422/409/429) показываются его же русским текстом. */

const KINDS: Array<[DocumentKind, string]> = [
  ["job_description", "Должностная инструкция"], ["regulation", "Положение"], ["other", "Прочее"],
];
const SCOPES: Array<[DocumentScope, string]> = [["all", "Все сотрудники"], ["department", "Отдел"], ["job_title", "Должность"]];

type Props = {
  /** Сколько документов ждут подписи этого человека — для счётчика на вкладке. */
  onPending?: (count: number) => void;
};

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || "").split(",")[1] || "");
    reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

function PdfLink({ id, label = "Открыть PDF" }: { id: string; label?: string }) {
  return <a href={documentsApi.fileUrl(id)} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>{label}</a>;
}

export function EmployeeDocuments({ onPending }: Props) {
  const [overview, setOverview] = useState<DocumentsOverview | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [creating, setCreating] = useState(false);

  const apply = useCallback((loaded: DocumentsOverview) => {
    setOverview(loaded);
    setError("");
    onPending?.(loaded.own.filter((item) => item.signed === null).length);
  }, [onPending]);

  const load = useCallback(async () => {
    try {
      apply(await documentsApi.overview());
    } catch (reason) {
      setError(failureText(reason, "Документы не загружены"));
    }
  }, [apply]);

  useEffect(() => { void load(); }, [load]);

  if (error && !overview) return <div className="error-banner" role="alert">{error}</div>;
  if (!overview) return <div className="empty">Загрузка…</div>;

  const unsigned = overview.own.filter((item) => item.signed === null);
  const history = overview.own.filter((item) => item.signed !== null)
    .sort((a, b) => String(b.signed?.signed_at).localeCompare(String(a.signed?.signed_at)));
  const changed = async (text: string) => { setMessage(text); await load(); };

  return <div className="lv-page">
    {message && <div className="success-banner" role="status">{message}</div>}
    {error && <div className="error-banner" role="alert">{error}</div>}

    <section className="panel lv-block" aria-label="К ознакомлению">
      <h2>К ознакомлению{unsigned.length > 0 && <span className="page-count">{unsigned.length}</span>}</h2>
      {unsigned.length > 0
        ? <DataTable rows={unsigned} columns={ownColumns(false)} rowKey={(item) => item.id} hideSearch pageSize={50}
          renderDetail={(item) => <SignDetail item={item} onSigned={(result) => { apply(result); setMessage(`Ознакомлен: «${item.title}».`); }} />} />
        : <p className="empty">Документов к ознакомлению нет — всё прочитано и подписано.</p>}
    </section>

    <section className="panel lv-block" aria-label="История ознакомления">
      <h2>История ознакомления{history.length > 0 && <span className="page-count">{history.length}</span>}</h2>
      {history.length > 0
        ? <DataTable rows={history} columns={ownColumns(true)} rowKey={(item) => item.id} hideSearch pageSize={50} />
        : <p className="empty">Вы ещё не подписывали документов.</p>}
    </section>

    {overview.hr && overview.journal && <section className="panel lv-block" aria-label="Управление документами">
      <h2>Управление документами{overview.journal.length > 0 && <span className="page-count">{overview.journal.length}</span>}
        {!creating && <button className="primary small ed-add" onClick={() => { setMessage(""); setCreating(true); }}>Новый документ</button>}
      </h2>
      {creating && <DocumentForm onCancel={() => setCreating(false)} onDone={async (text) => { setCreating(false); await changed(text); }} />}
      {overview.journal.length > 0
        ? <DataTable rows={overview.journal} columns={journalColumns()} rowKey={(item) => item.id} storageKey="employee-documents-journal"
          exportName="Документы с ознакомлением" pageSize={100}
          rowClassName={(item) => item.archived ? "ed-archived" : ""}
          renderDetail={(item) => <HrDetail item={item} onChanged={changed} />} />
        : !creating && <p className="empty">Документов пока нет — нажмите «Новый документ».</p>}
    </section>}
  </div>;
}

function ownColumns(history: boolean): DataColumn<OwnDocument>[] {
  const columns: DataColumn<OwnDocument>[] = [
    { key: "title", title: "Документ", wrap: true, value: (item) => item.title, render: (item) => <strong>{item.title}</strong> },
    { key: "kind", title: "Вид", width: 190, filter: true, value: (item) => item.kind_label },
    { key: "audience", title: "Для кого", width: 200, value: (item) => item.audience },
    { key: "version", title: "Редакция", width: 90, numeric: true, value: (item) => item.current?.version_no ?? "" },
  ];
  if (history) {
    columns.push(
      { key: "signed", title: "Подписан", width: 150, value: (item) => item.signed?.signed_at ?? "", render: (item) => formatDate(item.signed?.signed_at) },
      { key: "method", title: "Чем подписан", width: 120, value: (item) => item.signed?.method_label ?? "" },
    );
  } else {
    columns.push({ key: "uploaded", title: "Загружен", width: 150, value: (item) => item.current?.uploaded_at ?? "", render: (item) => formatDate(item.current?.uploaded_at) });
  }
  columns.push({ key: "pdf", title: "PDF", width: 110, value: () => "", render: (item) => item.current ? <PdfLink id={item.current.id} /> : "—" });
  return columns;
}

function journalColumns(): DataColumn<JournalDocument>[] {
  return [
    { key: "title", title: "Документ", wrap: true, value: (item) => item.title, render: (item) => <strong>{item.title}</strong> },
    { key: "kind", title: "Вид", width: 190, filter: true, value: (item) => item.kind_label },
    { key: "audience", title: "Для кого", width: 200, value: (item) => item.audience },
    { key: "version", title: "Редакция", width: 90, numeric: true, value: (item) => item.current?.version_no ?? "" },
    { key: "uploaded", title: "Загружен", width: 150, value: (item) => item.current?.uploaded_at ?? "", render: (item) => formatDate(item.current?.uploaded_at) },
    { key: "signed", title: "Ознакомлены", width: 120, numeric: true, value: (item) => item.readers ? item.signed / item.readers : 0, render: (item) => `${item.signed} из ${item.readers}` },
    {
      key: "state", title: "Состояние", width: 110, filter: true, value: (item) => item.archived ? "В архиве" : "В силе",
      render: (item) => <span className={`kit-status ${item.archived ? "tone-done" : "tone-stock"}`}>{item.archived ? "В архиве" : "В силе"}</span>,
    },
    { key: "pdf", title: "PDF", width: 110, value: () => "", render: (item) => item.current ? <PdfLink id={item.current.id} /> : "—" },
  ];
}

function SecretField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return <label>PIN или пароль для подписи
    <input type="password" autoComplete="current-password" aria-label="PIN или пароль для подписи" value={value} onChange={(event) => onChange(event.target.value)} />
  </label>;
}

function SignDetail({ item, onSigned }: { item: OwnDocument; onSigned: (overview: DocumentsOverview) => void }) {
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const current = item.current;
  if (!current) return null;

  async function sign() {
    setBusy(true);
    setError("");
    try {
      onSigned(await documentsApi.acknowledge((current as { id: string }).id, secret));
    } catch (reason) {
      setError(failureText(reason, "Подпись не принята"));
      setBusy(false);
    }
  }

  return <div className="lv-detail" onClick={(event) => event.stopPropagation()}>
    <dl className="ec-facts">
      <div className="ec-fact"><dt>Документ</dt><dd>{item.kind_label} · редакция {current.version_no} от {formatDate(current.uploaded_at)} · {current.uploaded_by_name}</dd></div>
      <div className="ec-fact"><dt>Файл</dt><dd><PdfLink id={current.id} label={`Открыть PDF: ${current.file_name || item.title}`} /></dd></div>
    </dl>
    {item.signed_earlier_version !== null && <p className="warning-inline">Вы подписали редакцию {item.signed_earlier_version}; в этой редакции документ изменён — подпишите заново.</p>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="lv-actions">
      <SecretField value={secret} onChange={setSecret} />
      <button className="primary small" disabled={busy || !secret} onClick={() => void sign()}>Подписать: ознакомлен</button>
    </div>
  </div>;
}

function DocumentForm({ onCancel, onDone }: { onCancel: () => void; onDone: (message: string) => Promise<void> }) {
  const [draft, setDraft] = useState<DocumentDraft>({ title: "", kind: "job_description", scope: "all", scope_value: "" });
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const needsValue = draft.scope !== "all";
  const valueLabel = draft.scope === "department" ? "Отдел" : "Должность";
  const invalid = !draft.title.trim() || !file || (needsValue && !draft.scope_value.trim());

  async function save() {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      await documentsApi.create({ ...draft, scope_value: needsValue ? draft.scope_value : "" }, file.name, await readBase64(file));
      await onDone(`Документ «${draft.title.trim()}» создан — адресаты увидят его в уведомлениях.`);
    } catch (reason) {
      setError(failureText(reason, "Документ не создан"));
      setBusy(false);
    }
  }

  return <div className="ed-form" role="group" aria-label="Новый документ">
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="form-grid">
      <label className="half">Название<input value={draft.title} maxLength={255} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
      <label>Вид
        <select value={draft.kind} onChange={(event) => setDraft({ ...draft, kind: event.target.value as DocumentKind })}>
          {KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label>Для кого
        <select value={draft.scope} onChange={(event) => setDraft({ ...draft, scope: event.target.value as DocumentScope, scope_value: "" })}>
          {SCOPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      {needsValue && <label>{valueLabel}<input value={draft.scope_value} maxLength={255} onChange={(event) => setDraft({ ...draft, scope_value: event.target.value })} /></label>}
      <label>Файл PDF<input type="file" accept="application/pdf,.pdf" aria-label="Файл PDF" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
    </div>
    <div className="form-actions">
      <button className="primary" disabled={busy || invalid} onClick={() => void save()}>Создать документ</button>
      <button className="link-button" disabled={busy} onClick={onCancel}>Отмена</button>
    </div>
  </div>;
}

function HrDetail({ item, onChanged }: { item: JournalDocument; onChanged: (message: string) => Promise<void> }) {
  const [versionNo, setVersionNo] = useState<number | null>(null);
  const [sheet, setSheet] = useState<DocumentSheet | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const stamp = `${item.current?.id ?? ""}${item.archived}${item.signed}`;

  useEffect(() => {
    let live = true;
    documentsApi.sheet(item.id, versionNo ?? undefined)
      .then((loaded) => { if (live) { setSheet(loaded); setError(""); } })
      .catch((reason) => { if (live) setError(failureText(reason, "Лист ознакомления не загружен")); });
    return () => { live = false; };
  }, [item.id, versionNo, stamp]);

  async function act(action: () => Promise<unknown>, text: string) {
    setBusy(true);
    setError("");
    try {
      await action();
      setConfirming(false);
      setVersionNo(null);
      await onChanged(text);
    } catch (reason) {
      setError(failureText(reason, "Действие не выполнено"));
    } finally {
      setBusy(false);
    }
  }

  const last = item.current;
  const signedRows = sheet?.rows.filter((row) => row.signed_at) ?? [];
  const waitingRows = sheet?.rows.filter((row) => !row.signed_at) ?? [];

  return <div className="lv-detail ed-detail" onClick={(event) => event.stopPropagation()}>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="ed-actions">
      <label className="ed-upload">Новая редакция (PDF)
        <input type="file" accept="application/pdf,.pdf" aria-label="Новая редакция (PDF)" disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void act(async () => documentsApi.addVersion(item.id, file.name, await readBase64(file)), `«${item.title}»: загружена новая редакция, адресаты уведомлены.`);
          }} />
      </label>
      <button className="secondary small" disabled={busy}
        onClick={() => void act(() => documentsApi.archive(item.id, !item.archived), item.archived ? `«${item.title}» возвращён в силу.` : `«${item.title}» отправлен в архив.`)}>
        {item.archived ? "Вернуть из архива" : "В архив"}
      </button>
      {last && !confirming && <button className="link-button danger-link" disabled={busy || item.signed > 0}
        title={item.signed > 0 ? "Редакцию уже подписали — удалить нельзя, отправьте документ в архив." : undefined}
        onClick={() => setConfirming(true)}>Удалить последнюю редакцию</button>}
      {last && confirming && <span className="ed-confirm">Удалить редакцию {last.version_no}?
        <button className="primary small" disabled={busy} onClick={() => void act(() => documentsApi.removeVersion(last.id), `Редакция ${last.version_no} удалена.`)}>Да, удалить</button>
        <button className="link-button" disabled={busy} onClick={() => setConfirming(false)}>Нет</button>
      </span>}
    </div>

    <div className="ed-versions">
      <label>Лист ознакомления — редакция
        <select value={sheet?.version.version_no ?? ""} onChange={(event) => setVersionNo(Number(event.target.value))}>
          {item.versions.map((version) => <option key={version.id} value={version.version_no}>
            Редакция {version.version_no} от {formatDate(version.uploaded_at)}
          </option>)}
        </select>
      </label>
      {sheet && <span className="ec-note">{sheet.is_current ? "действующая" : "прежняя"} · подписали {sheet.signed} из {sheet.readers}</span>}
      {sheet && <PdfLink id={sheet.version.id} label={`PDF редакции ${sheet.version.version_no}`} />}
    </div>

    {sheet ? <div className="ed-sheet">
      <div aria-label="Подписали">
        <h3 className="ec-sub-title">Подписали · {signedRows.length}</h3>
        {signedRows.length > 0 ? <ul className="ec-list">
          {signedRows.map((row) => <li key={row.user_id} className="ec-row">
            <span className="ec-main">{row.name}{!row.in_scope && <small className="ec-code">уже не в адресатах</small>}</span>
            <span className="ec-date">{formatDate(row.signed_at)} · {row.method_label}</span>
          </li>)}
        </ul> : <p className="ec-note">Пока никто.</p>}
      </div>
      <div aria-label="Не подписали">
        <h3 className="ec-sub-title">Не подписали · {waitingRows.length}</h3>
        {waitingRows.length > 0 ? <ul className="ec-list">
          {waitingRows.map((row) => <li key={row.user_id} className="ec-row">
            <span className="ec-main">{row.name}</span>
            <span className="ec-note">{[row.department, row.job_title].filter(Boolean).join(" · ")}</span>
          </li>)}
        </ul> : <p className="ec-note">Все подписали.</p>}
      </div>
    </div> : !error && <p className="empty">Загрузка…</p>}
  </div>;
}
