import { useEffect, useMemo, useState } from "react";
import { SuggestInput } from "./SuggestInput";
import { techcardsApi, type TemplateSheet, type TemplateSheetDraft } from "./api";
import { formatDate } from "./format";

const DRAFT_PREFIX = "Черновик — ";
const MIN_COLUMNS = 7;
const NEW_ROWS = 12;

function message(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

/** Сетка черновика — прямоугольник: ширина по самой длинной строке, не уже шаблона ТК (7 колонок). */
function rectangle(rows: string[][]): string[][] {
  const width = Math.max(MIN_COLUMNS, ...rows.map((row) => row.length));
  const source = rows.length ? rows : Array.from({ length: NEW_ROWS }, () => []);
  return source.map((row) => Array.from({ length: width }, (_, index) => row[index] ?? ""));
}

/** Правка черновика: то, что в SUPPLYMAP инженер делал в листе «Черновик — …» файла-хранилища. */
function DraftEditor({ draft, catalog, onClose, onSaved }: {
  draft: TemplateSheetDraft; catalog: TemplateSheet[]; onClose: () => void; onSaved: (text: string) => void;
}) {
  const [rows, setRows] = useState(() => rectangle(draft.rows ?? []));
  const base = draft.draftSheet.replace(DRAFT_PREFIX, "");
  const existing = catalog.find((item) => item.id === draft.existingTemplateId) ?? catalog.find((item) => item.title === base);
  const [form, setForm] = useState({ title: base, category: existing?.category ?? draft.category, description: existing?.description ?? draft.description });
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const categories = useMemo(() => [...new Set(catalog.map((item) => item.category).filter(Boolean))], [catalog]);

  function setCell(row: number, column: number, value: string) {
    setRows((current) => current.map((cells, index) => index === row ? cells.map((cell, at) => at === column ? value : cell) : cells));
  }

  async function storeRows() {
    await techcardsApi.editTemplateDraft(draft.id, rows);
  }

  async function saveDraft() {
    setBusy(true); setError(""); setNotice("");
    try { await storeRows(); setNotice("Черновик сохранён"); }
    catch (reason) { setError(message(reason, "Не удалось сохранить черновик")); }
    finally { setBusy(false); }
  }

  async function saveTemplate() {
    setBusy(true); setError(""); setNotice("");
    try {
      await storeRows();
      const result = await techcardsApi.saveTemplateDraft(draft.id, form);
      onSaved(`${result.action === "updated" ? "Шаблон обновлён" : "Шаблон создан"}: ${result.title}`);
    } catch (reason) {
      setError(message(reason, "Не удалось сохранить шаблон"));
    } finally { setBusy(false); }
  }

  return <article className="panel template-draft">
    <div className="panel-heading"><strong>{draft.draftSheet}</strong><span>{rows.length}×{rows[0]?.length ?? 0}</span></div>
    <div className="page-sub">Сетка шаблона: первая ячейка — заголовок «КОД | Наименование», далее строки листа техкарты (Операция / Контроль, Инструмент, Оборудование, Расходники…). При сохранении пустые хвосты строк и колонок отбрасываются.</div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    <div className="table-wrap"><table className="sheet-grid"><tbody>
      {rows.map((cells, row) => <tr key={row}>
        <th>{row + 1}</th>
        {cells.map((cell, column) => <td key={column}><input aria-label={`Ячейка ${row + 1}:${column + 1}`} value={cell} onChange={(event) => setCell(row, column, event.target.value)} /></td>)}
      </tr>)}
    </tbody></table></div>
    <div className="form-actions">
      <button className="secondary small" onClick={() => setRows((current) => [...current, current[0].map(() => "")])}>+ Строка</button>
      <button className="secondary small" onClick={() => setRows((current) => current.map((cells) => [...cells, ""]))}>+ Колонка</button>
      <button className="secondary" disabled={busy} onClick={() => void saveDraft()}>Сохранить черновик</button>
      <button className="primary" disabled={busy} onClick={() => setSaving(true)}>Сохранить шаблон</button>
      <button className="secondary" disabled={busy} onClick={onClose}>Закрыть</button>
    </div>
    {saving && <div className="panel">
      <div className="panel-heading"><strong>Сохранить шаблон из черновика</strong></div>
      <p className="page-sub">{existing ? `Пересохранение существующего шаблона «${existing.title}» (контент заменится).` : "Создание нового шаблона из черновика."}</p>
      <div className="form-grid">
        <label>Название<input aria-label="Название шаблона" value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /></label>
        <label>Категория<SuggestInput aria-label="Категория шаблона" options={categories} value={form.category} onChange={(category) => setForm({ ...form, category })} /></label>
        <label>Описание<textarea aria-label="Описание шаблона" rows={2} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
      </div>
      <div className="form-actions">
        <button className="primary" disabled={busy || !form.title.trim()} onClick={() => void saveTemplate()}>Сохранить</button>
        <button className="secondary" disabled={busy} onClick={() => setSaving(false)}>Отмена</button>
      </div>
    </div>}
  </article>;
}

/** «Шаблоны-листы»: каталог БД.ТК + черновики (SUPPLYMAP PortalApp openTcTplSheets). */
export function TcTemplateSheets({ canEdit, onBack, onChanged }: { canEdit: boolean; onBack: () => void; onChanged: () => void }) {
  const [list, setList] = useState<TemplateSheet[]>([]);
  const [drafts, setDrafts] = useState<TemplateSheetDraft[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [query, setQuery] = useState("");
  const [newTitle, setNewTitle] = useState<string | null>(null);
  const [open, setOpen] = useState<TemplateSheetDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    try {
      const result = await techcardsApi.templateSheets();
      setList(result.list); setDrafts(result.drafts); setLoaded(true);
    } catch (reason) {
      setError(message(reason, "Не удалось загрузить шаблоны-листы"));
    }
  }
  useEffect(() => { void load(); }, []);

  const shown = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("ru-RU");
    return list.filter((item) => !needle || `${item.title} ${item.category} ${item.description}`.toLocaleLowerCase("ru-RU").includes(needle));
  }, [list, query]);

  async function act(run: () => Promise<void>, fallback: string) {
    setBusy(true); setError(""); setNotice("");
    try { await run(); } catch (reason) { setError(message(reason, fallback)); } finally { setBusy(false); }
  }

  const openDraft = (templateId: string, title: string) => act(async () => {
    const draft = await techcardsApi.openTemplateDraft(templateId, title);
    setNewTitle(null);
    setOpen(draft);
    await load();
  }, "Не удалось открыть черновик");

  const resumeDraft = (draftId: string) => act(async () => { setOpen(await techcardsApi.templateDraft(draftId)); }, "Не удалось открыть черновик");

  const dropDraft = (draft: TemplateSheetDraft) => {
    if (!window.confirm("Удалить черновик без сохранения?")) return;
    void act(async () => { await techcardsApi.dropTemplateDraft(draft.id); await load(); }, "Не удалось удалить черновик");
  };

  const deleteSheet = (item: TemplateSheet) => {
    if (!window.confirm(`Удалить шаблон «${item.title}» из БД?`)) return;
    void act(async () => {
      await techcardsApi.deleteTemplateSheet(item.id);
      setNotice(`Шаблон удалён: ${item.title}`);
      await load();
      onChanged();
    }, "Не удалось удалить шаблон");
  };

  if (open) {
    return <DraftEditor key={open.id} draft={open} catalog={list} onClose={() => { setOpen(null); void load(); }} onSaved={(text) => {
      setOpen(null); setNotice(text); void load(); onChanged();
    }} />;
  }

  return <article className="panel template-sheets">
    <div className="form-actions"><button className="secondary" onClick={onBack}>← Назад</button><strong>Шаблоны-листы</strong></div>
    <p className="page-sub">Листы БД.ТК, из которых собирает генератор. «✏️» открывает черновик шаблона — правь его сетку здесь, потом «Сохранить шаблон». Новый шаблон строится на пустом черновике.</p>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    <div className="toolbar">
      {canEdit && <button className="secondary" disabled={busy} onClick={() => setNewTitle("")}>Новый шаблон</button>}
      <input aria-label="Поиск шаблона-листа" placeholder="Поиск по названию/категории…" value={query} onChange={(event) => setQuery(event.target.value)} />
    </div>
    {newTitle !== null && <div className="panel">
      <p className="page-sub">Создастся черновик — построй на нём шаблон, потом нажми «Сохранить шаблон».</p>
      <label>Название шаблона<input aria-label="Название нового шаблона" placeholder="CUT_WIRE_auto | Резка" value={newTitle} onChange={(event) => setNewTitle(event.target.value)} /></label>
      <div className="form-actions">
        <button className="primary" disabled={busy || !newTitle.trim()} onClick={() => void openDraft("", newTitle)}>Создать черновик</button>
        <button className="secondary" onClick={() => setNewTitle(null)}>Отмена</button>
      </div>
    </div>}
    {drafts.length > 0 && <div className="warning-inline"><b>Открытые черновики:</b>{" "}
      {drafts.map((draft) => <span key={draft.id} className="nowrap">{draft.draftSheet.replace(DRAFT_PREFIX, "")}{" "}
        {canEdit && <><button className="secondary small" disabled={busy} onClick={() => void resumeDraft(draft.id)}>Открыть</button>{" "}
        <button className="secondary small" disabled={busy} aria-label={`Удалить черновик ${draft.title}`} onClick={() => dropDraft(draft)}>✖</button></>}{" "}
      </span>)}
    </div>}
    <div className="table-wrap"><table><thead><tr><th>Название</th><th>Категория</th><th>Размер</th><th>Обновлён</th><th></th></tr></thead><tbody>
      {shown.map((item) => <tr key={item.id}>
        <td>{item.title}{item.description && <div className="muted">{item.description}</div>}</td>
        <td>{item.category}</td><td>{item.size}</td><td>{formatDate(item.updatedAt.slice(0, 10))}</td>
        <td className="nowrap">{canEdit && <>
          <button className="secondary small" disabled={busy} aria-label={`Редактировать ${item.title}`} onClick={() => void openDraft(item.id, "")}>✏️</button>{" "}
          <button className="secondary small" disabled={busy} aria-label={`Удалить ${item.title}`} title="Удалить шаблон" onClick={() => deleteSheet(item)}>🗑</button>
        </>}</td>
      </tr>)}
      {loaded && !shown.length && <tr><td colSpan={5}><div className="empty">Ничего не найдено.</div></td></tr>}
    </tbody></table></div>
    <div className="page-sub">Всего: {list.length}</div>
  </article>;
}
