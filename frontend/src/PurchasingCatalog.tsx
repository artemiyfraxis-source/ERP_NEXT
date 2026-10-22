import { Fragment, useCallback, useEffect, useState } from "react";
import { failureText } from "./api";
import { NBSP, formatDay } from "./format";
import { UpdNumber, kopecks } from "./PricesSections";
import { purchasingPricesApi, type CatalogPage, type CatalogPoint, type UpdItemsPlan } from "./purchasingPricesApi";

/** «Каталог закупок»: positions bought by UPD, grouped by part number (else by name),
 *  with price points and suppliers; and «Завести позиции из УПД» (preview first, then the chosen ones). */
export function PurchasingCatalog({ readOnly = false, onOpenUpd }: { readOnly?: boolean; onOpenUpd?: (id: string) => void } = {}) {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<CatalogPage | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => { setPage(1); }, [q]);
  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      purchasingPricesApi.catalog(q, page)
        .then((next) => { if (live) { setData(next); setError(""); } })
        .catch((reason) => { if (live) setError(failureText(reason, "Каталог не загрузился")); });
    }, q ? 200 : 0);
    return () => { live = false; clearTimeout(timer); };
  }, [q, page]);

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  return (
    <section className="panel table-panel">
      <div className="panel-heading"><h2>Каталог закупок</h2><span>что, у кого и почём покупали — по разобранным и проверенным УПД</span></div>
      <div className="inline-action purchasing-toolbar">
        <input type="search" aria-label="Поиск по каталогу закупок" placeholder="Слова в любом порядке: название или артикул" value={q} onChange={(event) => setQ(event.target.value)} />
        {!readOnly && <button type="button" className="secondary" onClick={() => setCreating(true)}>Завести позиции из УПД</button>}
      </div>
      <p className="hint">Цены за единицу с НДС, как в УПД. Доставка, услуги, М-15, корректировки и документы на проверке в расчёт не идут.</p>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!error && !data && <div className="empty">Загружаю…</div>}
      {data && (data.entries.length === 0 ? <div className="empty">Нет позиций. Они появятся, когда в реестре будут разобранные УПД.</div> : (
        <div className="table-wrap data-table">
          <table className="grid" aria-label="Каталог закупок">
            <thead>
              <tr>
                <th>Наименование</th><th>Артикул</th><th>Ед.</th><th className="num">Закупок</th><th className="num">Поставщиков</th>
                <th className="num">Последняя цена</th><th>Когда, у кого</th><th className="num">Мин.</th><th className="num">Макс.</th>
              </tr>
            </thead>
            <tbody>
              {data.entries.map((entry) => (
                <Fragment key={entry.key}>
                  <tr className="clickable" aria-selected={open === entry.key} onClick={() => setOpen(open === entry.key ? null : entry.key)}>
                    <td className="text">{entry.name}</td><td>{entry.article || "—"}</td><td>{entry.unit || "—"}</td>
                    <td className="num">{entry.purchases}</td><td className="num">{entry.suppliers}</td>
                    <td className="num">{kopecks(entry.last_price)}</td>
                    <td className="text">{entry.last_date ? formatDay(entry.last_date) : "—"}{entry.last_supplier ? ` · ${entry.last_supplier}` : ""}</td>
                    <td className="num">{kopecks(entry.min_price)}</td><td className="num">{kopecks(entry.max_price)}</td>
                  </tr>
                  {open === entry.key && <tr><td colSpan={9}><CatalogPoints priceKey={entry.key} onOpenUpd={onOpenUpd} /></td></tr>}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      {data && data.total > data.page_size && (
        <div className="inline-action">
          <button type="button" className="secondary small" disabled={page <= 1} onClick={() => setPage(page - 1)}>Назад</button>
          <span>Страница {page} из {pages}</span>
          <button type="button" className="secondary small" disabled={page >= pages} onClick={() => setPage(page + 1)}>Вперёд</button>
        </div>
      )}
      {creating && <UpdItemsDialog onClose={() => setCreating(false)} />}
    </section>
  );
}

function CatalogPoints({ priceKey, onOpenUpd }: { priceKey: string; onOpenUpd?: (id: string) => void }) {
  const [points, setPoints] = useState<CatalogPoint[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    purchasingPricesApi.points(priceKey)
      .then((next) => { if (live) setPoints(next); })
      .catch((reason) => { if (live) setError(failureText(reason, "Покупки не загрузились")); });
    return () => { live = false; };
  }, [priceKey]);
  if (error) return <span className="error-text">{error}</span>;
  if (!points) return <span className="muted">Загружаю…</span>;
  return (
    <table className="grid prices-points" aria-label="Покупки позиции">
      <tbody>
        {points.map((p, index) => (
          <tr key={`${p.upd_id}:${index}`}>
            <td>{p.date ? formatDay(p.date) : "—"}</td><td className="text">{p.supplier_name || "—"}</td>
            <td><UpdNumber id={p.upd_id} number={p.number} onOpenUpd={onOpenUpd} /></td>
            <td className="num">{p.quantity ?? "—"}{NBSP}{p.unit}</td><td className="num">{kopecks(p.unit_price_with_vat)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function UpdItemsDialog({ onClose }: { onClose: () => void }) {
  const [plan, setPlan] = useState<UpdItemsPlan | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await purchasingPricesApi.plan();
      setPlan(next);
      setChosen(new Set(next.candidates.map((c) => c.key)));
      setError("");
    } catch (reason) {
      setError(failureText(reason, "Не удалось подготовить список"));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const toggle = (key: string) => setChosen((now) => { const next = new Set(now); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  const apply = async () => {
    setBusy(true);
    setError("");
    try {
      const done = await purchasingPricesApi.apply([...chosen]);
      setNote(`Создано позиций: ${done.created}; названий поставщиков запомнено: ${done.aliases}.`);
      await load();
    } catch (reason) {
      setError(failureText(reason, "Не удалось завести позиции"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dialog-overlay" role="presentation">
      <div className="panel move-dialog wide" role="dialog" aria-label="Завести позиции из УПД">
        <h2>Завести позиции из УПД</h2>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {note && <p className="hint" role="status">{note}</p>}
        {!plan && !error && <div className="empty">Считаю…</div>}
        {plan && (
          <>
            <p>
              Товары из УПД, которых нет в номенклатуре: <strong>{plan.candidates.length}</strong>. Уже известно: {plan.known}.
              {" "}Пропущено услуг: {plan.skipped_services}, авансов: {plan.skipped_advances}; УПД в очереди разбора: {plan.waiting_documents}.
            </p>
            {plan.candidates.length > 0 && (
              <div className="table-wrap history-table prices-plan">
                <table className="grid" aria-label="Позиции из УПД">
                  <thead><tr><th /><th>Наименование</th><th>Артикул</th><th>Ед.</th><th className="num">Строк</th><th>Поставщики</th></tr></thead>
                  <tbody>
                    {plan.candidates.map((c) => (
                      <tr key={c.key}>
                        <td><input type="checkbox" aria-label={`Завести ${c.name}`} checked={chosen.has(c.key)} onChange={() => toggle(c.key)} /></td>
                        <td className="text">{c.name}</td><td>{c.article || "—"}</td><td>{c.unit}</td><td className="num">{c.lines}</td>
                        <td className="text">{c.suppliers.join(", ") || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
        <div className="move-actions">
          <button type="button" className="primary" disabled={busy || !plan || chosen.size === 0} onClick={() => void apply()}>Завести выбранные ({chosen.size})</button>
          <button type="button" className="secondary" onClick={onClose}>Закрыть</button>
        </div>
      </div>
    </div>
  );
}
