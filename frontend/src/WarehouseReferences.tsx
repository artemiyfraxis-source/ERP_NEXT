import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { SuggestInput } from "./SuggestInput";
import { warehouseApi, type WarehouseBulkResult, type WarehouseItemTypes, type WarehouseJournal, type WarehouseRackLayout, type WarehouseReferenceEdit } from "./api";
import { decodeStockTable, stockTableBytes, stockTableEdits, stockTableText } from "./warehouseTable";

const errorText = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

/** SK-14 «Справочник типов» (SUPPLYMAP openStock05Types): the dropdown list is edited here, without code.
 *  Renaming changes the type in EVERY stock row — that is how an old name leaves the dropdown. */
export function ItemTypesPanel({ onClose, onChanged }: {
  onClose: () => void;
  onChanged: (types: WarehouseItemTypes, renamed: boolean) => Promise<void> | void;
}) {
  const [data, setData] = useState<WarehouseItemTypes | null>(null);
  const [list, setList] = useState("");
  const [message, setMessage] = useState<{ text: string; bad?: boolean } | null>(null);
  const [renaming, setRenaming] = useState("");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    const loaded = await warehouseApi.itemTypes();
    setData(loaded);
    setList(loaded.types.join("\n"));
    return loaded;
  }

  useEffect(() => {
    load().catch((reason) => setMessage({ text: errorText(reason, "Не удалось загрузить справочник"), bad: true }));
  }, []);

  async function run(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    try {
      await operation();
    } catch (reason) {
      setMessage({ text: errorText(reason, "Ошибка"), bad: true });
    } finally {
      setBusy(false);
    }
  }

  const save = () => run(async () => {
    setMessage({ text: "Сохраняю…" });
    const saved = await warehouseApi.saveItemTypes(list.split(/\r?\n/));
    setData(saved);
    setList(saved.types.join("\n"));
    setMessage({ text: saved.message });
    await onChanged(saved, false);
  });

  const rename = () => run(async () => {
    const result = await warehouseApi.renameItemType(renaming, target);
    setRenaming("");
    setTarget("");
    const reloaded = await load();
    setMessage({ text: result.message });
    await onChanged(reloaded, true);
  });

  const counts = data?.counts ?? {};
  const used = Object.keys(counts).sort((a, b) => a.localeCompare(b, "ru"));
  const inDictionary = new Set((data?.types ?? []).map((type) => type.toLowerCase()));

  return <section className="panel item-types-panel" aria-label="Справочник типов">
    <div className="manual-stock-head"><h2>Справочник типов</h2><button type="button" className="link-button" onClick={onClose}>Закрыть</button></div>
    <p className="page-sub">Один тип — одна строка; порядок строк — порядок в подсказках. Тип, которого нет в справочнике, но который стоит у позиций на складе, в подсказках тоже останется (хвостом) — чтобы убрать его совсем, переименуй его ниже в правильный.</p>
    <textarea aria-label="Список типов" rows={10} value={list} disabled={!data} onChange={(event) => { setMessage(null); setList(event.target.value); }} />
    <div className="manual-stock-actions"><button className="primary" type="button" disabled={busy || !data} onClick={() => void save()}>Сохранить список</button></div>
    {message && <p className={message.bad ? "danger-text" : "page-sub"} role="status">{message.text}</p>}
    <h3>Типы на складе</h3>
    <table className="grid compact">
      <thead><tr><th>Тип</th><th className="num">Строк</th><th /></tr></thead>
      <tbody>{used.length ? used.map((type) => <tr key={type}>
        <td>{type}{!inDictionary.has(type.toLowerCase()) && <span className="danger-text"> нет в справочнике</span>}</td>
        <td className="num">{counts[type]}</td>
        <td>{renaming === type
          ? <form className="inline-action" onSubmit={(event) => { event.preventDefault(); void rename(); }}>
            <label>Новый тип<SuggestInput aria-label={`Новый тип для «${type}»`} options={data?.choices ?? []} required value={target} onChange={setTarget} /></label>
            <button className="primary small" disabled={busy || !target.trim()}>Переименовать {counts[type]} строк</button>
            <button type="button" className="link-button" onClick={() => setRenaming("")}>Отмена</button>
          </form>
          : <button type="button" className="secondary small" disabled={busy} onClick={() => { setRenaming(type); setTarget(""); }}>Переименовать</button>}
        </td>
      </tr>) : <tr><td colSpan={3} className="page-sub">У позиций склада тип не проставлен.</td></tr>}</tbody>
    </table>
  </section>;
}

/** SK-16 «Журнал склада» (SUPPLYMAP openStock05Log): who did what to the stock and when — receipts, moves, row edits,
 *  write-offs, returns and issues, newest first. The search runs on the server; the last 400 matches come back. */
/** `initialQuery` — «История» партии (openStock05Log(grn)): журнал сразу отобран по её ГРН. */
export function StockJournalPanel({ onClose, initialQuery = "" }: { onClose: () => void; initialQuery?: string }) {
  const [query, setQuery] = useState(initialQuery);
  const [journal, setJournal] = useState<WarehouseJournal | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const request = useRef(0);

  useEffect(() => {
    const mine = ++request.current;
    setLoading(true);
    const timer = setTimeout(() => {
      warehouseApi.journal(query)
        .then((loaded) => { if (mine === request.current) { setJournal(loaded); setError(""); } })
        .catch((reason) => { if (mine === request.current) setError(errorText(reason, "Не удалось загрузить журнал")); })
        .finally(() => { if (mine === request.current) setLoading(false); });
    }, query ? 450 : 0);
    return () => clearTimeout(timer);
  }, [query]);

  const rows = journal?.rows ?? [];
  return <section className="panel stock-journal-panel" aria-label="Журнал склада">
    <div className="manual-stock-head"><h2>Журнал склада</h2><button type="button" className="link-button" onClick={onClose}>Закрыть</button></div>
    <p className="page-sub">Кто, когда и что сделал со складом: приход, перемещения, правки строк, выдачи по заявкам, списания и возвраты. Свежие сверху. Поиск — по ГРН, наименованию, фамилии или слову из события («перемещение», «возврат»).</p>
    <input className="search" aria-label="Поиск по журналу склада" placeholder="поиск: OLD.26.0000012, возврат, Иванов…" value={query} onChange={(event) => setQuery(event.target.value)} />
    {error && <p className="danger-text" role="alert">{error}</p>}
    {!error && loading && !journal && <p className="page-sub">Загружаю…</p>}
    {!error && journal && (rows.length ? <>
      <div className="table-wrap"><table className="grid compact" aria-label="События склада">
        <thead><tr><th>Когда</th><th>Что</th><th>ГРН / №</th><th>Наименование</th><th>Подробно</th><th>Склад</th><th>Кто</th></tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index}>
          <td className="num">{new Date(row.occurred_at).toLocaleString("ru-RU")}</td><td>{row.kind}</td><td>{row.lot}</td>
          <td>{row.name}</td><td className="text">{row.event}</td><td>{row.warehouse}</td><td>{row.who}</td>
        </tr>)}</tbody>
      </table></div>
      <p className="page-sub">Показано {rows.length} из {journal.total}{journal.truncated ? " — уточни поиск, чтобы увидеть более старые" : ""}.</p>
    </> : <p className="page-sub">Ничего не нашлось.</p>)}
  </section>;
}

/** «Выгрузить»: the whole stock as a table for Excel (SUPPLYMAP wh05Export). */
export async function downloadStockTable() {
  const { rows } = await warehouseApi.exportRows();
  const blob = new Blob([stockTableBytes(stockTableText(rows))], { type: "text/csv;charset=utf-16le" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const today = new Date();
  const two = (value: number) => String(value).padStart(2, "0");
  link.href = url;
  link.download = `Склад ${today.getFullYear()}-${two(today.getMonth() + 1)}-${two(today.getDate())}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** «Загрузить правки склада» (SUPPLYMAP openStock05Import): check first — the preview shows what changes,
 *  only the second button writes. The administrator only; the server refuses anyone else. */
export function StockImportPanel({ onClose, onApplied }: { onClose: () => void; onApplied: () => Promise<void> | void }) {
  const [text, setText] = useState("");
  const [message, setMessage] = useState<{ text: string; bad?: boolean } | null>(null);
  const [edits, setEdits] = useState<WarehouseReferenceEdit[] | null>(null);
  const [preview, setPreview] = useState<WarehouseBulkResult | null>(null);
  const [busy, setBusy] = useState(false);

  function readFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    void file.arrayBuffer().then((buffer) => {
      setText(decodeStockTable(new Uint8Array(buffer)));
      setPreview(null);
      setMessage({ text: "Файл прочитан — жми «Проверить»." });
    });
  }

  async function check() {
    const parsed = stockTableEdits(text);
    setPreview(null);
    if ("error" in parsed) {
      setMessage({ text: parsed.error, bad: true });
      return;
    }
    setBusy(true);
    setMessage({ text: `Проверяю ${parsed.edits.length} строк…` });
    try {
      const result = await warehouseApi.importTable(parsed.edits, false);
      setEdits(parsed.edits);
      setPreview(result);
      setMessage(result.count
        ? { text: `Изменится полей: ${result.count} в строках: ${result.rows}. Проверь и подтверди.` }
        : { text: "Отличий от склада нет — записывать нечего." });
    } catch (reason) {
      setMessage({ text: errorText(reason, "Ошибка"), bad: true });
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!edits) return;
    setBusy(true);
    setMessage({ text: "Записываю…" });
    try {
      const result = await warehouseApi.importTable(edits, true);
      setPreview(null);
      setEdits(null);
      setMessage({ text: `Записано: ${result.count} полей в ${result.rows} строках. Каждая правка — в журнале склада.` });
      await onApplied();
    } catch (reason) {
      setMessage({ text: errorText(reason, "Ошибка"), bad: true });
    } finally {
      setBusy(false);
    }
  }

  return <section className="panel stock-import-panel" aria-label="Загрузить правки склада">
    <div className="manual-stock-head"><h2>Загрузить правки склада</h2><button type="button" className="link-button" onClick={onClose}>Закрыть</button></div>
    <p className="page-sub">Порядок: «Выгрузить» → правишь файл в Excel → выбираешь его здесь (или вставляешь диапазон вместе со строкой заголовков) → «Проверить». Сначала покажем, что изменится, и только по второй кнопке запишем. Строки находятся по Склад + ГРН + Пакет — эти три колонки не меняй. Загружаются справочные поля; количество из файла не загружается — его меняют списание, возврат и приход. Колонку можно удалить из файла — тогда она не трогается; пустая ячейка в оставленной колонке стирает значение (кроме наименования).</p>
    <input type="file" aria-label="Файл таблицы" accept=".csv,.txt,.tsv" onChange={readFile} />
    <textarea aria-label="Таблица склада" rows={6} placeholder="…или вставь сюда диапазон из Excel со строкой заголовков" value={text} onChange={(event) => { setPreview(null); setText(event.target.value); }} />
    <div className="manual-stock-actions"><button className="primary" type="button" disabled={busy} onClick={() => void check()}>Проверить</button></div>
    {message && <p className={message.bad ? "danger-text" : "page-sub"} role="status">{message.text}</p>}
    {preview && preview.missing_count > 0 && <p className="danger-text">Не найдены на складе ({preview.missing_count}): {preview.missing.join(", ")}{preview.missing_count > preview.missing.length ? " …" : ""}</p>}
    {preview && preview.errors.length > 0 && <p className="danger-text">Пропущены строки ({preview.errors.length}): {preview.errors.map((row) => `${row.grn}${row.package ? ` / ${row.package}` : ""} — ${row.error}`).join("; ")}</p>}
    {preview && preview.count > 0 && <>
      <div className="table-wrap"><table className="grid compact" aria-label="Что изменится">
        <thead><tr><th>Склад</th><th>ГРН</th><th>Наименование</th><th>Поле</th><th>Было</th><th>Станет</th></tr></thead>
        <tbody>{preview.changes.map((change, index) => <tr key={index}>
          <td>{change.warehouse}</td><td>{change.grn}{change.package ? ` / ${change.package}` : ""}</td><td>{change.name}</td>
          <td>{change.column}</td><td>{change.was || "—"}</td><td><strong>{change.now || "— (стереть)"}</strong></td>
        </tr>)}</tbody>
      </table></div>
      {preview.count > preview.changes.length && <p className="page-sub">Показаны первые {preview.changes.length} из {preview.count}.</p>}
      <div className="manual-stock-actions"><button className="primary" type="button" disabled={busy} onClick={() => void apply()}>Записать {preview.count} изменений</button></div>
    </>}
  </section>;
}

/** «Раскладка» склада (PortalApp.html, portalStock05PlacesSave): сколько стеллажей, полок по умолчанию и
 *  стеллажи со своим числом полок. По ней проверяется каждое место при приходе, вводе и перемещении. */
export function RackLayoutPanel({ onClose }: { onClose: () => void }) {
  const [layout, setLayout] = useState<WarehouseRackLayout | null>(null);
  const [special, setSpecial] = useState<Array<{ rack: string; shelves: string }>>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    warehouseApi.rackLayout()
      .then(({ layout: loaded }) => {
        setLayout(loaded);
        setSpecial(Object.entries(loaded.perRack).map(([rack, shelves]) => ({ rack, shelves: String(shelves) })));
      })
      .catch((reason) => setError(errorText(reason, "Не удалось загрузить раскладку")));
  }, []);

  async function save() {
    if (!layout) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const perRack = Object.fromEntries(special.filter((row) => row.rack.trim()).map((row) => [row.rack.trim(), Number(row.shelves)]));
      const result = await warehouseApi.saveRackLayout({ ...layout, perRack });
      setLayout(result.layout);
      setSpecial(Object.entries(result.layout.perRack).map(([rack, shelves]) => ({ rack, shelves: String(shelves) })));
      setMessage(result.message);
    } catch (reason) {
      setError(errorText(reason, "Раскладка не сохранена"));
    } finally {
      setSaving(false);
    }
  }

  return <section className="panel" aria-label="Раскладка стеллажей">
    <div className="manual-stock-head"><h2>Раскладка стеллажей</h2><button type="button" className="link-button" onClick={onClose}>Закрыть</button></div>
    <p className="page-sub">Сколько стеллажей и полок (буквы A…Z). Место вида 1F4 принимается, только если такой стеллаж и полка есть.</p>
    {error && <p className="danger-text" role="alert">{error}</p>}
    {layout && <form className="inline-action" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <label>Стеллажей<input aria-label="Стеллажей" type="number" min="1" max="99" required value={layout.racks} onChange={(event) => setLayout({ ...layout, racks: Number(event.target.value) })} /></label>
      <label>Полок по умолчанию<input aria-label="Полок по умолчанию" type="number" min="1" max="26" required value={layout.shelves} onChange={(event) => setLayout({ ...layout, shelves: Number(event.target.value) })} /></label>
      {special.map((row, index) => <span key={index} className="inline-action">
        <label>Стеллаж №<input aria-label={`Особый стеллаж ${index + 1}`} type="number" min="1" max="99" value={row.rack} onChange={(event) => setSpecial((current) => current.map((item, position) => position === index ? { ...item, rack: event.target.value } : item))} /></label>
        <label>полок<input aria-label={`Полок на особом стеллаже ${index + 1}`} type="number" min="1" max="26" value={row.shelves} onChange={(event) => setSpecial((current) => current.map((item, position) => position === index ? { ...item, shelves: event.target.value } : item))} /></label>
        <button type="button" className="link-button" onClick={() => setSpecial((current) => current.filter((_, position) => position !== index))}>✕</button>
      </span>)}
      <button type="button" className="secondary small" onClick={() => setSpecial((current) => [...current, { rack: "", shelves: String(layout.shelves) }])}>+ особый стеллаж</button>
      <button className="primary small" disabled={saving}>{saving ? "Сохраняем…" : "Сохранить раскладку"}</button>
    </form>}
    {message && <p className="page-sub" role="status">{message}</p>}
  </section>;
}
