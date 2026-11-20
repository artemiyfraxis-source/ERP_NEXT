import { useEffect, useMemo, useState, type FormEvent } from "react";
import { failureText } from "./api";
import { EMPTY, formatNumber, formatQty, formatWallMoment, toNumber, unitLabel } from "./format";
import { manualJobsApi, type ManualJob, type ManualJobLot, type ManualJobMaterialView } from "./manualJobsApi";
import { PageWindow } from "./PageWindow";
import "./manualJobs.css";

/** Окно «Материал» ручной работы (срез S3): работа сама ничего не списывает — здесь склад один раз решает, что с
 *  материалом. Расход подсказан: провод = (годные + брак) × длина + отходы наладки, контакты = изделия × концы;
 *  партии под провод / контакт работы (склад станка первым) или найденные поиском; «Списать с партии» — только с
 *  правкой «Склада», «Не списывать» — с причиной. Сервер: `workflows/manual_job_stock.py`. */

type Kind = "wire" | "contact";

const METRES: Record<string, number> = { "м": 1, "мм": 1000, "км": 0.001 };

/** Сколько единиц партии в одной единице расхода (м — провод, шт — контакты); null — не пересчитать. */
function factor(kind: Kind, unit: string): number | null {
  const name = unitLabel(unit);
  if (kind === "wire") return METRES[name] ?? null;
  return name === "шт" ? 1 : null;
}

const round = (value: number, kind: Kind) => (kind === "contact" ? Math.ceil(value - 1e-9) : Math.round(value * 1000) / 1000);
const plain = (value: number) => String(value).replace(".", ",");

/** Жадно раскладывает расход по партиям в их порядке, не больше доступного: {партия: количество в её единицах}. */
function spread(lots: ManualJobLot[], amount: number, kind: Kind): Record<string, string> {
  const result: Record<string, string> = {};
  let left = amount;
  for (const lot of lots) {
    const scale = factor(kind, lot.unit);
    const available = toNumber(lot.available) ?? 0;
    if (left <= 1e-9 || scale === null || available <= 0) continue;
    const take = Math.min(round(left * scale, kind), available);
    if (take <= 0) continue;
    result[lot.id] = plain(take);
    left -= take / scale;
  }
  return result;
}

function LotTable({ title, lots, quantities, setQuantity, disabled }: {
  title: string; lots: ManualJobLot[]; quantities: Record<string, string>; setQuantity: (id: string, value: string) => void;
  disabled: boolean;
}) {
  return <div className="data-table mj-lots" role="group" aria-label={title}>
    <table className="grid">
      <caption>{title}</caption>
      <thead><tr><th>Склад</th><th>ГРН</th><th>Артикул</th><th className="grow">Наименование</th><th>Место</th>
        <th className="num">Доступно</th><th className="num">Списать</th></tr></thead>
      <tbody>{lots.map((lot) => <tr key={lot.id} className={lot.here ? undefined : "mj-elsewhere"}>
        <td>{lot.warehouse}</td>
        <td className="mj-num">{lot.grn}{lot.package ? `-${lot.package}` : ""}</td>
        <td>{lot.article || EMPTY}</td>
        <td>{lot.name || EMPTY}</td>
        <td>{lot.storage_location || EMPTY}</td>
        <td className="num mj-num">{formatQty(lot.available, lot.unit)}</td>
        <td className="num"><span className="mj-qty">
          <input inputMode="decimal" autoComplete="off" aria-label={`Списать с ${lot.grn}`} disabled={disabled}
            value={quantities[lot.id] ?? ""} placeholder="0" onChange={(event) => setQuantity(lot.id, event.target.value)} />
          <span>{unitLabel(lot.unit)}</span></span></td>
      </tr>)}</tbody>
    </table>
  </div>;
}

function Issued({ view }: { view: ManualJobMaterialView }) {
  if (!view.issues.length) return null;
  return <div className="data-table mj-lots" role="group" aria-label="Списано с партий">
    <table className="grid">
      <caption>Списано с партий</caption>
      <thead><tr><th>Склад</th><th>ГРН</th><th className="grow">Наименование</th><th className="num">Списано</th><th>Кто</th><th>Когда</th></tr></thead>
      <tbody>{view.issues.map((item) => <tr key={item.writeoff_id}>
        <td>{item.warehouse}</td><td className="mj-num">{item.grn}{item.package ? `-${item.package}` : ""}</td>
        <td>{item.name || EMPTY}</td><td className="num mj-num">{formatQty(item.quantity, item.unit)}</td>
        <td>{item.by || EMPTY}</td><td className="mj-num">{item.at ? formatWallMoment(item.at) : EMPTY}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

function Decide({ view, onSaved }: { view: ManualJobMaterialView; onSaved: (view: ManualJobMaterialView) => void }) {
  const job = view.job;
  const pieces = view.pieces ?? 0;
  const length = toNumber(view.length_mm) ?? 0;
  const [waste, setWaste] = useState("");
  const [ends, setEnds] = useState(1);
  const [found, setFound] = useState<ManualJobLot[]>(view.found);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const wasteValue = waste.trim() ? toNumber(waste) : 0;
  const wireMetres = (pieces * length) / 1000 + (wasteValue ?? 0);
  const contacts = pieces * ends;
  const [quantities, setQuantities] = useState<Record<string, string>>(() => ({
    ...spread(view.wire.lots, (pieces * length) / 1000, "wire"), ...spread(view.contact.lots, pieces, "contact") }));
  const lots = useMemo(() => {
    const all = new Map<string, ManualJobLot>();
    for (const lot of [...view.wire.lots, ...view.contact.lots, ...found]) all.set(lot.id, lot);
    return all;
  }, [view.wire.lots, view.contact.lots, found]);
  const shownFound = found.filter((lot) => !view.wire.lots.some((item) => item.id === lot.id) && !view.contact.lots.some((item) => item.id === lot.id));
  const setQuantity = (id: string, value: string) => setQuantities((current) => ({ ...current, [id]: value }));
  const filled = Object.entries(quantities).filter(([, value]) => value.trim());
  let problem = "";
  for (const [id, value] of filled) {
    const lot = lots.get(id);
    const number = toNumber(value);
    if (!lot) continue;
    if (number === null || number <= 0) { problem = `${lot.grn}: количество — число больше нуля`; break; }
    if (number > (toNumber(lot.available) ?? 0)) { problem = `${lot.grn}: доступно ${formatQty(lot.available, lot.unit)}`; break; }
  }
  const lines = filled.filter(([id]) => lots.has(id)).map(([id, value]) => ({
    lot_id: id, lot_version: lots.get(id)!.version, quantity: String(toNumber(value)) }));
  if (!problem && !lines.length) problem = "Укажите, сколько списать хотя бы с одной партии";
  if (wasteValue === null) problem = "Отходы наладки — число, м";

  const run = async (action: () => Promise<ManualJobMaterialView>, fallback: string) => {
    setBusy(true);
    setError("");
    try {
      onSaved(await action());
    } catch (failure) {
      setError(failureText(failure, fallback));
    } finally {
      setBusy(false);
    }
  };
  const search = async (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    setError("");
    try {
      const answer = await manualJobsApi.material(job.id, query);
      setFound(answer.found);
      if (!answer.found.length) setError(`По «${query.trim()}» партий с остатком нет`);
    } catch (failure) {
      setError(failureText(failure, "Поиск партий не выполнен"));
    } finally {
      setSearching(false);
    }
  };
  const issue = (event: FormEvent) => {
    event.preventDefault();
    if (problem || busy || !view.can_issue) return;
    void run(() => manualJobsApi.issue(job.id, job.version, lines), "Материал не списан");
  };

  return <>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <section className="mj-usage" aria-label="Расход">
      <h3>Расход</h3>
      <div className="form-grid mj-grid">
        {job.wire && <>
          <p className="mj-calc">Провод: {formatNumber(pieces)} шт × {formatNumber(length)} мм = <b>{formatQty(wireMetres, "м")}</b></p>
          <label>Отходы наладки, м<input inputMode="decimal" autoComplete="off" placeholder="0" value={waste}
            aria-invalid={wasteValue === null || undefined} onChange={(event) => setWaste(event.target.value)} /></label>
        </>}
        {job.contact && <>
          <p className="mj-calc">Контакты: {formatNumber(pieces)} шт × {ends} = <b>{formatQty(contacts, "шт")}</b></p>
          <label>Концов на изделие<select value={ends} onChange={(event) => setEnds(Number(event.target.value))}>
            <option value={1}>1</option><option value={2}>2</option></select></label>
        </>}
      </div>
    </section>
    <form className="mj-form" onSubmit={issue} aria-label="Списать с партии">
      {job.wire && <div className="mj-lot-head"><h3>Провод {job.wire}</h3>
        {view.can_issue && view.wire.lots.length > 0 && <button type="button" className="secondary small"
          onClick={() => setQuantities((current) => ({ ...current, ...Object.fromEntries(view.wire.lots.map((lot) => [lot.id, ""])), ...spread(view.wire.lots, wireMetres, "wire") }))}>
          Подставить {formatQty(wireMetres, "м")}</button>}</div>}
      {job.wire && (view.wire.lots.length
        ? <LotTable title={`Партии провода ${job.wire}`} lots={view.wire.lots} quantities={quantities} setQuantity={setQuantity} disabled={!view.can_issue} />
        : <p className="empty">Партий «{job.wire}» с остатком не нашлось — найдите партию ниже</p>)}
      {job.contact && <div className="mj-lot-head"><h3>Контакт {job.contact}</h3>
        {view.can_issue && view.contact.lots.length > 0 && <button type="button" className="secondary small"
          onClick={() => setQuantities((current) => ({ ...current, ...Object.fromEntries(view.contact.lots.map((lot) => [lot.id, ""])), ...spread(view.contact.lots, contacts, "contact") }))}>
          Подставить {formatQty(contacts, "шт")}</button>}</div>}
      {job.contact && (view.contact.lots.length
        ? <LotTable title={`Партии контакта ${job.contact}`} lots={view.contact.lots} quantities={quantities} setQuantity={setQuantity} disabled={!view.can_issue} />
        : <p className="empty">Партий «{job.contact}» с остатком не нашлось — найдите партию ниже</p>)}
      {shownFound.length > 0 && <LotTable title="Найденные партии" lots={shownFound} quantities={quantities} setQuantity={setQuantity} disabled={!view.can_issue} />}
      <div className="mj-actions">
        <button type="submit" className="primary" disabled={Boolean(problem) || busy || !view.can_issue}>
          {busy ? "Списываю…" : `Списать с партии${lines.length > 1 ? ` (${lines.length} партии)` : ""}`}</button>
        <span className="mj-hint" aria-live="polite">{view.can_issue ? problem : "Списывает склад — нужна правка раздела «Склад»"}</span>
        <button type="button" className="secondary small mj-cancel-toggle" aria-expanded={noteOpen} onClick={() => setNoteOpen(!noteOpen)}>
          Не списывать…</button>
      </div>
    </form>
    <form className="mj-search" role="search" aria-label="Найти другую партию" onSubmit={search}>
      <label>Другая партия<input type="search" autoComplete="off" placeholder="ГРН, артикул или наименование…" value={query}
        onChange={(event) => setQuery(event.target.value)} /></label>
      <button type="submit" className="secondary" disabled={!query.trim() || searching}>{searching ? "Ищу…" : "Найти"}</button>
    </form>
    {noteOpen && <form className="mj-cancel" aria-label="Не списывать" onSubmit={(event) => {
      event.preventDefault();
      if (note.trim() && !busy) void run(() => manualJobsApi.notIssued(job.id, job.version, note), "Решение не сохранено");
    }}>
      <label>Почему не списывать<input name="material_note" autoComplete="off" required value={note}
        placeholder="Например: обрезки из отходов…" onChange={(event) => setNote(event.target.value)} /></label>
      <button type="submit" className="secondary" disabled={!note.trim() || busy}>Не списывать</button>
    </form>}
  </>;
}

export function ManualJobMaterial({ job, close, onSaved }: {
  job: ManualJob; close: () => void; onSaved?: (job: ManualJob) => void;
}) {
  const [view, setView] = useState<ManualJobMaterialView | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    manualJobsApi.material(job.id, "", controller.signal).then(setView)
      .catch((reason) => { if (!controller.signal.aborted) setError(failureText(reason, "Материал работы не загружен")); });
    return () => controller.abort();
  }, [job.id]);
  const saved = (next: ManualJobMaterialView) => { setView(next); onSaved?.(next.job); };
  const current = view?.job ?? job;
  const title = `Материал ${current.number}`;
  const pending = current.status === "done" && current.material === "pending";
  return <PageWindow label={title} close={close} width={1100}>
    <section className="panel mj-window">
      <header className="mj-window-head">
        <h2>{title}</h2>
        {current.material_text && <span className={`mj-status tone-${current.material_tone}`}>{current.material_text}</span>}
        <button type="button" className="secondary small" onClick={close}>Закрыть</button>
      </header>
      <dl className="mj-facts">
        <div><dt>Станок</dt><dd>{[current.inventory, current.machine].filter(Boolean).join(" · ") || EMPTY}</dd></div>
        <div><dt>Склад станка</dt><dd>{view?.site || EMPTY}</dd></div>
        <div><dt>Зачем</dt><dd>{current.purpose}</dd></div>
        <div><dt>Изделий, шт</dt><dd>{current.good === null ? EMPTY : `${formatNumber((current.good ?? 0) + (current.defect ?? 0))} (годных ${formatNumber(current.good)}, брак ${formatNumber(current.defect ?? 0)})`}</dd></div>
        <div><dt>Провод</dt><dd>{current.wire || EMPTY}</dd></div>
        <div><dt>Контакт</dt><dd>{current.contact || EMPTY}</dd></div>
        <div><dt>Длина, мм</dt><dd>{current.length_mm ?? EMPTY}</dd></div>
        {current.material === "not_issued" && <div><dt>Почему не списывать</dt><dd>{current.material_note}</dd></div>}
      </dl>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!view && !error && <p className="empty">Загрузка…</p>}
      {view && <Issued view={view} />}
      {view && current.status === "running" && <p className="empty">Работа ещё идёт — материал решают после завершения</p>}
      {view && current.status === "cancelled" && <p className="empty">Работа отменена — материал по ней не списывают</p>}
      {view && pending && (view.can_decide
        ? <Decide key={view.job.version} view={view} onSaved={saved} />
        : <p className="empty">Материал ждёт решения склада — у вас только просмотр</p>)}
    </section>
  </PageWindow>;
}
