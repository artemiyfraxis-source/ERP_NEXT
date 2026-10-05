import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, failureText } from "./api";
import {
  ITEM_CATEGORIES, categoryLabel, purchasingItemsApi as itemsApi,
  type ImportResult, type ItemCard, type ItemCategory, type ItemInput, type ItemList, type ItemRow,
} from "./purchasingItemsApi";

/** «Номенклатура» (option references.items): the item master — search, card with aliases and price history, merge,
 *  and «Собрать из закупок» (preview first, then apply). */
const money = (value: string | null) => (value === null ? "—" : `${Number(value).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`);
const demandText = (demand: { unit: string; quantity: string }[]) => (demand.length ? demand.map((d) => `${d.quantity.replace(".", ",")} ${d.unit}`).join(", ") : "—");
const dayText = (iso: string) => iso.split("-").reverse().join(".");
const emptyForm: ItemInput = { name: "", part_number: "", manufacturer: "", category: null, item_type: "", unit: "", notes: "" };

type Supplier = { id: string; name: string };

export function PurchasingItems({ readOnly = false }: { readOnly?: boolean } = {}) {
  const [q, setQ] = useState("");
  const [category, setCategory] = useState<ItemCategory | "">("");
  const [hasPrice, setHasPrice] = useState(false);
  const [page, setPage] = useState(1);
  const [list, setList] = useState<ItemList | null>(null);
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [reload, setReload] = useState(0);
  const refresh = useCallback(() => setReload((n) => n + 1), []);

  useEffect(() => { setPage(1); }, [q, category, hasPrice]);
  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      itemsApi.list({ q, category, hasPrice, page })
        .then((next) => { if (live) { setList(next); setError(""); } })
        .catch((reason) => { if (live) setError(failureText(reason, "Номенклатура не загрузилась")); });
    }, q ? 200 : 0);
    return () => { live = false; clearTimeout(timer); };
  }, [q, category, hasPrice, page, reload]);

  const total = list ? Object.values(list.counts).reduce((sum, n) => sum + n, 0) : 0;
  const pages = list ? Math.max(1, Math.ceil(list.total / list.page_size)) : 1;
  return (
    <section className="panel table-panel">
      <div className="panel-heading"><h2>Номенклатура</h2><span>позиции с артикулом, названиями у поставщиков и историей цен</span></div>
      <div className="inline-action purchasing-toolbar">
        <input type="search" aria-label="Поиск по номенклатуре" placeholder="Слова в любом порядке: название, артикул, изготовитель" value={q} onChange={(event) => setQ(event.target.value)} />
        <label><input type="checkbox" checked={hasPrice} onChange={(event) => setHasPrice(event.target.checked)} /> Только с ценой</label>
        {!readOnly && <button type="button" className="primary" onClick={() => setCreating(true)}>+ Позиция</button>}
        {!readOnly && <button type="button" className="secondary" onClick={() => setImporting(true)}>Собрать из закупок</button>}
      </div>
      <div className="calendar-chips" role="group" aria-label="Категория">
        <button type="button" className={`secondary small${category === "" ? " active" : ""}`} aria-pressed={category === ""} onClick={() => setCategory("")}>Все {list ? total : ""}</button>
        {ITEM_CATEGORIES.map((c) => (
          <button key={c.key} type="button" className={`secondary small${category === c.key ? " active" : ""}`} aria-pressed={category === c.key} onClick={() => setCategory(c.key)}>
            {c.label} {list ? list.counts[c.key] ?? 0 : ""}
          </button>
        ))}
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!error && !list && <div className="empty">Загружаю…</div>}
      {list && (list.items.length === 0 ? <div className="empty">Позиций нет. «Собрать из закупок» создаст их из строк закупки.</div> : (
        <div className="table-wrap data-table">
          <table className="grid" aria-label="Номенклатура">
            <thead>
              <tr>
                <th>Наименование</th><th>Артикул</th><th>Изготовитель</th><th>Категория</th><th>Ед.</th>
                <th className="num">Последняя цена</th><th className="num">Закупок</th><th>Спрос</th>
              </tr>
            </thead>
            <tbody>
              {list.items.map((row) => <ItemTableRow key={row.id} row={row} onOpen={() => setOpenId(row.id)} />)}
            </tbody>
          </table>
        </div>
      ))}
      {list && list.total > list.page_size && (
        <div className="inline-action">
          <button type="button" className="secondary small" disabled={page <= 1} onClick={() => setPage(page - 1)}>Назад</button>
          <span>Страница {page} из {pages}</span>
          <button type="button" className="secondary small" disabled={page >= pages} onClick={() => setPage(page + 1)}>Вперёд</button>
        </div>
      )}
      {openId && <ItemCardDialog id={openId} readOnly={readOnly} onClose={() => setOpenId(null)} onChanged={refresh} />}
      {creating && <CreateDialog onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); refresh(); setOpenId(id); }} />}
      {importing && <ImportDialog onClose={() => setImporting(false)} onApplied={refresh} />}
    </section>
  );
}

function ItemTableRow({ row, onOpen }: { row: ItemRow; onOpen: () => void }) {
  return (
    <tr className="clickable" onClick={onOpen}>
      <td className="text"><button type="button" className="link-button table-link" onClick={(event) => { event.stopPropagation(); onOpen(); }}>{row.name}</button></td>
      <td>{row.part_number ?? "—"}</td>
      <td>{row.manufacturer ?? "—"}</td>
      <td>{categoryLabel(row.category)}</td>
      <td>{row.unit}</td>
      <td className="num">{money(row.last_price)}</td>
      <td className="num">{row.purchases}</td>
      <td>{demandText(row.demand)}</td>
    </tr>
  );
}

function ItemFormFields({ form, setForm, disabled }: { form: ItemInput; setForm: (next: ItemInput) => void; disabled: boolean }) {
  const text = (key: keyof ItemInput, label: string) => (
    <label>{label}
      <input aria-label={label} disabled={disabled} value={(form[key] as string | null | undefined) ?? ""} onChange={(event) => setForm({ ...form, [key]: event.target.value })} />
    </label>
  );
  return (
    <div className="move-grid two">
      {text("name", "Наименование")}
      {text("part_number", "Артикул")}
      {text("manufacturer", "Изготовитель")}
      <label>Категория
        <select aria-label="Категория" disabled={disabled} value={form.category ?? ""} onChange={(event) => setForm({ ...form, category: (event.target.value || null) as ItemCategory | null })}>
          <option value="">Определить по названию</option>
          {ITEM_CATEGORIES.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
      </label>
      {text("item_type", "Тип")}
      {text("unit", "Ед. изм.")}
      <label style={{ gridColumn: "1 / -1" }}>Заметка
        <input aria-label="Заметка" disabled={disabled} value={form.notes ?? ""} onChange={(event) => setForm({ ...form, notes: event.target.value })} />
      </label>
    </div>
  );
}

function CreateDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const [form, setForm] = useState<ItemInput>(emptyForm);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const made = await itemsApi.create(form);
      onCreated(made.id);
    } catch (reason) {
      setError(failureText(reason, "Позиция не создана"));
      setBusy(false);
    }
  };
  return (
    <div className="dialog-overlay" role="presentation">
      <form className="panel move-dialog" role="dialog" aria-label="Новая позиция" onSubmit={submit}>
        <h2>Новая позиция</h2>
        <ItemFormFields form={form} setForm={setForm} disabled={busy} />
        {error && <div className="error-banner" role="alert">{error}</div>}
        <div className="move-actions">
          <button type="button" className="secondary" onClick={onClose}>Отмена</button>
          <button type="submit" className="primary" disabled={busy || !form.name.trim()}>Создать</button>
        </div>
      </form>
    </div>
  );
}

function ImportDialog({ onClose, onApplied }: { onClose: () => void; onApplied: () => void }) {
  const [preview, setPreview] = useState<ImportResult | null>(null);
  const [done, setDone] = useState<ImportResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    itemsApi.importFromProcurement(true)
      .then((next) => { if (live) setPreview(next); })
      .catch((reason) => { if (live) setError(failureText(reason, "Не удалось разобрать закупки")); });
    return () => { live = false; };
  }, []);
  const apply = async () => {
    setBusy(true);
    setError("");
    try {
      setDone(await itemsApi.importFromProcurement(false));
      onApplied();
    } catch (reason) {
      setError(failureText(reason, "Не удалось собрать номенклатуру"));
    } finally {
      setBusy(false);
    }
  };
  const shown = done ?? preview;
  return (
    <div className="dialog-overlay" role="presentation">
      <div className="panel move-dialog wide" role="dialog" aria-label="Собрать из закупок">
        <h2>Собрать из закупок</h2>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {!shown && !error && <div className="empty">Разбираю строки закупки…</div>}
        {shown && (
          <>
            <p className="page-sub" role="status">
              {done ? "Готово: " : "Будет: "}
              создано позиций — {shown.created}, связано с существующими — {shown.linked_groups}, строк закупки — {shown.lines}, названий — {shown.aliases}.
            </p>
            {shown.groups.length === 0 ? <div className="empty">Новых строк закупки для сборки нет.</div> : (
              <div className="table-wrap history-table">
                <table className="grid" aria-label="Что будет собрано">
                  <thead><tr><th>Группа</th><th>Наименование</th><th>Артикул</th><th>Категория</th><th className="num">Строк</th><th>Действие</th></tr></thead>
                  <tbody>
                    {shown.groups.map((group) => (
                      <tr key={`${group.kind}:${group.key}`}>
                        <td>{group.kind === "article" ? "по артикулу" : "по названию"}</td>
                        <td className="text">{group.name}</td>
                        <td>{group.part_number ?? "—"}</td>
                        <td>{categoryLabel(group.category)}</td>
                        <td className="num">{group.lines}</td>
                        <td className="text">{group.action === "create" ? "создать позицию" : `связать с «${group.existing_name}»${group.archived ? " (в архиве)" : ""}`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
        <div className="move-actions">
          <button type="button" className="secondary" onClick={onClose}>Закрыть</button>
          {!done && <button type="button" className="primary" disabled={busy || !preview || preview.groups.length === 0} onClick={apply}>Применить</button>}
        </div>
      </div>
    </div>
  );
}

function suppliersFrom(payload: unknown): Supplier[] {
  const rows = Array.isArray(payload) ? payload : ((payload as { suppliers?: unknown; items?: unknown } | null)?.suppliers ?? (payload as { items?: unknown } | null)?.items);
  return Array.isArray(rows) ? rows.filter((r): r is Supplier => typeof r?.id === "string" && typeof r?.name === "string") : [];
}

function ItemCardDialog({ id, readOnly, onClose, onChanged }: { id: string; readOnly: boolean; onClose: () => void; onChanged: () => void }) {
  const [card, setCard] = useState<ItemCard | null>(null);
  const [form, setForm] = useState<ItemInput>(emptyForm);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [aliasText, setAliasText] = useState("");
  const [aliasSupplier, setAliasSupplier] = useState("");
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [merging, setMerging] = useState(false);
  const [mergeQuery, setMergeQuery] = useState("");
  const [mergeFound, setMergeFound] = useState<ItemRow[]>([]);

  const load = useCallback(async () => {
    try {
      const next = await itemsApi.card(id);
      setCard(next);
      setForm({ name: next.name, part_number: next.part_number ?? "", manufacturer: next.manufacturer ?? "", category: next.category, item_type: next.item_type ?? "", unit: next.unit, notes: next.notes ?? "" });
      setError("");
    } catch (reason) {
      setError(failureText(reason, "Карточка не загрузилась"));
    }
  }, [id]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    // The supplier directory is another feature (references.suppliers): without it aliases are generic only.
    api<unknown>("/api/purchasing/suppliers").then((payload) => setSuppliers(suppliersFrom(payload))).catch(() => setSuppliers([]));
  }, []);
  useEffect(() => {
    if (!merging || !mergeQuery.trim()) { setMergeFound([]); return; }
    let live = true;
    itemsApi.list({ q: mergeQuery, category: "", hasPrice: false, page: 1 })
      .then((next) => { if (live) setMergeFound(next.items.filter((row) => row.id !== id)); })
      .catch(() => { if (live) setMergeFound([]); });
    return () => { live = false; };
  }, [merging, mergeQuery, id]);

  const run = async (action: () => Promise<unknown>, done: string, reloadCard = true) => {
    setError("");
    setNote("");
    try {
      await action();
      setNote(done);
      onChanged();
      if (reloadCard) await load();
    } catch (reason) {
      setError(failureText(reason, "Не удалось сохранить"));
    }
  };

  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!card) return;
    void run(() => itemsApi.update(card.id, card.version, { ...form, category: form.category ?? card.category }), "Сохранено");
  };
  const addAlias = (event: FormEvent) => {
    event.preventDefault();
    if (!card || !aliasText.trim()) return;
    void run(async () => { await itemsApi.addAlias(card.id, aliasText.trim(), aliasSupplier || null); setAliasText(""); }, "Название добавлено");
  };

  return (
    <div className="dialog-overlay" role="presentation">
      <div className="panel move-dialog wide" role="dialog" aria-label="Карточка позиции">
        <h2>{card ? card.name : "Карточка позиции"}{card?.archived ? " (в архиве)" : ""}</h2>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {note && <p className="hint" role="status">{note}</p>}
        {!card && !error && <div className="empty">Загружаю…</div>}
        {card && (
          <>
            <form className="dialog-form" onSubmit={save}>
              <ItemFormFields form={form} setForm={setForm} disabled={readOnly || card.archived} />
              {!readOnly && !card.archived && <div className="move-actions"><button type="submit" className="primary" disabled={!(form.name ?? "").trim()}>Сохранить</button></div>}
            </form>

            <h3>Названия</h3>
            {card.aliases.length === 0 ? <p className="muted">Других названий нет.</p> : (
              <ul aria-label="Названия">
                {card.aliases.map((alias) => (
                  <li key={alias.id}>
                    {alias.text} <span className="muted">— {alias.supplier_name ?? "у всех поставщиков"}</span>
                    {!readOnly && <button type="button" className="link-button danger-text" aria-label={`Удалить название ${alias.text}`} onClick={() => void run(() => itemsApi.deleteAlias(card.id, alias.id), "Название удалено")}>удалить</button>}
                  </li>
                ))}
              </ul>
            )}
            {!readOnly && (
              <form className="inline-action" onSubmit={addAlias}>
                <input aria-label="Новое название" placeholder="Как это называет поставщик" value={aliasText} onChange={(event) => setAliasText(event.target.value)} />
                <select aria-label="Поставщик названия" value={aliasSupplier} onChange={(event) => setAliasSupplier(event.target.value)}>
                  <option value="">у всех поставщиков</option>
                  {suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}
                </select>
                <button type="submit" className="secondary" disabled={!aliasText.trim()}>Добавить название</button>
              </form>
            )}

            <h3>Закупки и спрос</h3>
            <p>
              Связано строк закупки: <strong>{card.linked_lines}</strong>. Спрос (открытые строки): <strong>{demandText(card.demand)}</strong>.
              {" "}Цена с НДС: мин. {money(card.price.min)}, средняя {money(card.price.avg)}, последняя {money(card.price.last)}.
            </p>
            {card.history.length === 0 ? <p className="muted">Оплаченных закупок нет.</p> : (
              <div className="table-wrap history-table">
                <table className="grid" aria-label="История цен">
                  <thead><tr><th>Дата</th><th>Поставщик</th><th className="num">Цена с НДС</th><th className="num">Кол-во</th></tr></thead>
                  <tbody>
                    {card.history.map((point) => (
                      <tr key={point.line_id}>
                        <td>{dayText(point.date)}</td><td className="text">{point.supplier ?? "—"}</td>
                        <td className="num">{money(point.unit_price_with_vat)}</td><td className="num">{point.quantity.replace(".", ",")} {point.unit}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {card.quotes.length > 0 && (
              <>
                <h3>Коммерческие предложения</h3>
                <div className="table-wrap history-table">
                  <table className="grid" aria-label="КП по позиции">
                    <thead><tr><th>Дата</th><th>Поставщик</th><th className="num">Цена с НДС</th><th className="num">MOQ</th></tr></thead>
                    <tbody>
                      {card.quotes.map((quote) => (
                        <tr key={`${quote.quote_id}:${quote.date}:${quote.supplier}`}>
                          <td>{dayText(quote.date)}</td><td className="text">{quote.supplier}{quote.analog ? " (аналог)" : ""}</td>
                          <td className="num">{quote.declined ? "отказ" : money(quote.unit_price_with_vat)}</td><td className="num">{quote.moq ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}

            {!readOnly && !card.archived && (
              <>
                {merging && (
                  <div className="dialog-form">
                    <input aria-label="Найти позицию для объединения" placeholder="Какую позицию влить в эту карточку" value={mergeQuery} onChange={(event) => setMergeQuery(event.target.value)} />
                    {mergeFound.map((row) => (
                      <div key={row.id} className="move-lot">
                        <strong>{row.name}</strong>
                        <span>{row.part_number ?? "без артикула"} · строк закупки: {row.linked_lines}</span>
                        <button type="button" className="secondary small" onClick={() => void run(async () => { await itemsApi.merge(card.id, row.id); setMerging(false); setMergeQuery(""); }, `«${row.name}» объединена с этой позицией`)}>
                          Влить «{row.name}» сюда
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </>
        )}
        <div className="move-actions">
          {card && !readOnly && !card.archived && <button type="button" className="link-button" onClick={() => setMerging(!merging)}>Объединить с…</button>}
          {card && !readOnly && !card.archived && <button type="button" className="link-button danger-text" onClick={() => void run(() => itemsApi.archive(card.id), "Позиция в архиве")}>В архив</button>}
          <button type="button" className="secondary" onClick={onClose}>Закрыть</button>
        </div>
      </div>
    </div>
  );
}
