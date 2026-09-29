import { useEffect, useMemo, useState, type FormEvent } from "react";
import { receivingApi, type SupplierReturnCandidate } from "./api";

/** RC-6 «Возврат поставщику» (SUPPLYMAP SupplierReturn.gs): принятые позиции одной поставки уходят
 *  поставщику с общей причиной; по каждой, что лежит на складе, ставится списание. */
export function SupplierReturnPanel({ onDone, onClose }: { onDone: () => void; onClose: () => void }) {
  const [lines, setLines] = useState<SupplierReturnCandidate[]>([]);
  const [reasons, setReasons] = useState<string[]>([]);
  const [modes, setModes] = useState<Record<string, string>>({});
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState("");
  const [reason, setReason] = useState("");
  const [mode, setMode] = useState<"wait" | "close">("close");
  const [claim, setClaim] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [report, setReport] = useState("");

  async function load() {
    setError("");
    try {
      const result = await receivingApi.supplierReturnCandidates();
      setLines(result.lines);
      setReasons(result.reasons);
      setModes(result.modes);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить принятые позиции");
    }
  }

  useEffect(() => { void load(); }, []);

  const shown = useMemo(() => {
    const words = filter.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return lines;
    return lines.filter((line) => {
      const text = [line.legacy_id, line.name, line.article, line.supplier, line.grn ?? ""].join(" ").toLowerCase();
      return words.every((word) => text.includes(word));
    });
  }, [lines, filter]);

  function toggle(line: SupplierReturnCandidate, checked: boolean) {
    setPicked((current) => {
      const next = { ...current };
      if (checked) next[line.id] = line.fact_quantity ?? line.accepted_quantity ?? "";
      else delete next[line.id];
      return next;
    });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const items = lines
      .filter((line) => line.id in picked)
      .map((line) => ({ id: line.id, version: line.version, ...(picked[line.id] ? { quantity: picked[line.id] } : {}) }));
    if (!items.length) { setError("Не выбрано ни одной позиции."); return; }
    if (!reason) { setError("Укажи причину возврата."); return; }
    setBusy(true);
    setError("");
    try {
      const result = await receivingApi.returnToSupplier({ items, reason, mode, claim: claim.trim(), comment: comment.trim() });
      setReport(result.message);
      setPicked({});
      await load();
      onDone();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Возврат не оформлен");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel supplier-return" aria-label="Возврат поставщику">
      <div className="procurement-title">
        <div><strong>Возврат поставщику</strong><small>Принятые позиции: отметьте, что уходит поставщику. Один акт — одна позиция, причина общая.</small></div>
        <button type="button" className="secondary small" onClick={onClose}>Закрыть</button>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {report && <div className="success-banner" role="status">{report}</div>}
      <form onSubmit={submit}>
        <label>Поиск<input value={filter} placeholder="заказ, поставщик, ГРН, наименование" onChange={(event) => setFilter(event.target.value)} /></label>
        <div className="table-wrap"><table className="grid wide">
          <thead><tr><th /><th>Позиция</th><th>Поставщик</th><th>ГРН</th><th>На складе</th><th>Вернуть</th></tr></thead>
          <tbody>
            {shown.map((line) => (
              <tr key={line.id}>
                <td><input type="checkbox" aria-label={`Вернуть ${line.legacy_id}`} checked={line.id in picked} onChange={(event) => toggle(line, event.target.checked)} /></td>
                <td>{line.legacy_id}<br /><small>{line.name || line.article}</small></td>
                <td>{line.supplier}</td>
                <td>{line.grn ?? "—"}</td>
                <td>{line.stock_balance !== null ? `${line.stock_balance} ${line.unit} · ${line.stock_warehouse}` : "не разложена"}</td>
                <td>{line.id in picked && <input aria-label={`Количество ${line.legacy_id}`} type="number" min="0.000001" step="any" value={picked[line.id]} onChange={(event) => setPicked((current) => ({ ...current, [line.id]: event.target.value }))} />}</td>
              </tr>
            ))}
            {!shown.length && <tr><td colSpan={6}>Принятых позиций нет.</td></tr>}
          </tbody>
        </table></div>
        <div className="facts-grid">
          <label>Причина<select required value={reason} onChange={(event) => setReason(event.target.value)}><option value="">Выберите…</option>{reasons.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label>№ претензии<input value={claim} onChange={(event) => setClaim(event.target.value)} /></label>
          <label className="wide">Комментарий<input value={comment} onChange={(event) => setComment(event.target.value)} /></label>
          <fieldset className="check-field wide"><legend>Что с заказом</legend>
            {Object.entries(modes).map(([value, label]) => (
              <label key={value}><input type="radio" name="supplier-return-mode" checked={mode === value} onChange={() => setMode(value as "wait" | "close")} />{label}</label>
            ))}
          </fieldset>
        </div>
        <button className="primary small" disabled={busy}>{busy ? "Оформляем…" : `Оформить возврат (${Object.keys(picked).length})`}</button>
      </form>
    </section>
  );
}
