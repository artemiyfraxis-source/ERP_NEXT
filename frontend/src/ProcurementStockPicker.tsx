import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, failureText, type ProcurementLine } from "./api";

/* «Со склада»: позицию берём с нашего склада, а не покупаем. Закупщик выбирает партию, указывает количество и
   резервирует её; полное покрытие закрывает строку без денег, частичное уменьшает «КОЛ-ВО (факт)» до остатка. */

export type StockCover = {
  id: string; line_id: string; label: string; lot_id: string; warehouse: string; grn: string; package: string;
  storage_location: string; unit: string; quantity: string; consumed: string; consumed_by: string | null;
  active: boolean; created_at: string; created_by: string; released_at: string | null;
};

export type StockLot = {
  id: string; version: number; warehouse: string; grn: string; package: string; article: string; series: string;
  name: string; item_type: string; purpose: string; unit: string; storage_location: string;
  balance: string; reserved: string; available: string; unit_matches: boolean;
  holders: StockCover[]; reserve_note: string;
};

type Candidates = { line: ProcurementLine; query: string; lots: StockLot[]; covers: StockCover[] };

/** Поля строки, которые добавил склад: сколько закрыто со склада и сколько осталось купить. */
export type StockFields = { stock_covered?: string | null; to_buy?: string | null };

export const stockApi = {
  candidates: (lineId: string, q?: string) =>
    api<Candidates>(`/api/procurement/${lineId}/stock-candidates${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  cover: (lineId: string, version: number, lotId: string, quantity: string) =>
    api<{ line: ProcurementLine; cover: StockCover }>(`/api/procurement/${lineId}/stock-cover`, {
      method: "POST", body: JSON.stringify({ version, lot_id: lotId, quantity }),
    }),
  release: (lineId: string, version: number, coverId?: string) =>
    api<{ line: ProcurementLine; released: StockCover[] }>(`/api/procurement/${lineId}/stock-cover/release`, {
      method: "POST", body: JSON.stringify({ version, cover_id: coverId }),
    }),
};

export function coveredOf(line: ProcurementLine): number {
  return Number((line as ProcurementLine & StockFields).stock_covered ?? 0) || 0;
}

export function toBuyOf(line: ProcurementLine): string {
  return (line as ProcurementLine & StockFields).to_buy ?? line.calculated_quantity;
}

const num = (value: string) => Number(String(value).replace(",", ".")) || 0;
const fmt = (value: number) => String(Math.round(value * 1e6) / 1e6).replace(".", ",");

export function ProcurementStockPicker({ line, disabled, onDone, onClose }: {
  line: ProcurementLine;
  disabled: boolean;
  /** Резерв поставлен или строка устарела — таблица перечитывает строки. */
  onDone: (message: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [lots, setLots] = useState<StockLot[]>([]);
  const [covers, setCovers] = useState<StockCover[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [lotId, setLotId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [busy, setBusy] = useState(false);
  const toBuy = num(toBuyOf(line));

  async function search(q?: string) {
    setLoading(true);
    setError("");
    try {
      const result = await stockApi.candidates(line.id, q);
      setLots(result.lots);
      setCovers(result.covers);
      if (q === undefined) setQuery(result.query);
      setLotId("");
    } catch (reason) {
      setError(failureText(reason, "Склад не ответил"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void search(); }, [line.id]);

  function pick(lot: StockLot) {
    if (!lot.unit_matches) return;
    setLotId(lot.id);
    setQuantity(fmt(Math.min(num(lot.available), toBuy)));
  }

  async function reserve() {
    const lot = lots.find((item) => item.id === lotId);
    if (!lot) return;
    setBusy(true);
    setError("");
    try {
      const result = await stockApi.cover(line.id, line.version, lot.id, quantity.trim().replace(",", "."));
      onDone(`${line.legacy_id}: со склада ${result.cover.quantity} ${result.cover.unit} (ГРН ${result.cover.grn})`);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        setError("Строка изменилась — таблица обновлена, откройте «Со склада» ещё раз.");
        onDone("");
      } else {
        setError(failureText(reason, "Резерв не поставлен"));
      }
    } finally {
      setBusy(false);
    }
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    void search(query.trim());
  }

  const quantityOk = num(quantity) > 0;
  return (
    <div className="stock-picker">
      <form className="stock-search" onSubmit={submitSearch}>
        <strong>Со склада · {line.legacy_id}</strong>
        <input aria-label="Поиск на складе" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="артикул или наименование" />
        <button type="submit" className="secondary small" disabled={loading}>Найти</button>
        <small>К закупке: {fmt(toBuy)} {line.calculated_unit}{coveredOf(line) > 0 ? ` · уже со склада: ${fmt(coveredOf(line))}` : ""}</small>
        <button type="button" className="secondary small" onClick={onClose}>Закрыть</button>
      </form>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {covers.some((cover) => cover.active) && (
        <small>Уже зарезервировано: {covers.filter((cover) => cover.active).map((cover) => `${cover.quantity} ${cover.unit} (ГРН ${cover.grn}, ${cover.warehouse})`).join("; ")}</small>
      )}
      <div className="inwork-scroll">
        <table className="inwork-table stock-lots">
          <thead>
            <tr><th aria-label="Выбор" /><th>Склад</th><th>ГРН</th><th>Пакет</th><th>Место</th><th>Наименование / артикул</th><th>Доступно</th><th>Ед.</th></tr>
          </thead>
          <tbody>
            {lots.map((lot) => (
              <tr key={lot.id} className={lot.unit_matches ? undefined : "muted"} title={lot.unit_matches ? lot.reserve_note || undefined : `Единица склада «${lot.unit}» не совпадает с «${line.calculated_unit}»`}>
                <td><input type="radio" name={`lot-${line.id}`} aria-label={`Партия ${lot.grn}`} checked={lotId === lot.id} disabled={!lot.unit_matches || disabled} onChange={() => pick(lot)} /></td>
                <td>{lot.warehouse}</td>
                <td>{lot.grn}</td>
                <td>{lot.package}</td>
                <td>{lot.storage_location}</td>
                <td>{lot.name}{lot.article ? <small> · {lot.article}</small> : null}{!lot.unit_matches && <small> · другая единица</small>}</td>
                <td className="num">{lot.available}</td>
                <td>{lot.unit}</td>
              </tr>
            ))}
            {!lots.length && <tr><td colSpan={8} className="empty">{loading ? "Ищем на складе…" : "На складе ничего не найдено — измените запрос."}</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="stock-reserve">
        <label>Количество<input aria-label="Количество со склада" inputMode="decimal" value={quantity} disabled={!lotId} onChange={(event) => setQuantity(event.target.value)} /></label>
        <button type="button" className="primary small" disabled={disabled || busy || !lotId || !quantityOk} onClick={() => void reserve()}>{busy ? "Резервируем…" : "Зарезервировать"}</button>
      </div>
    </div>
  );
}
