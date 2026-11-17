import { useEffect, useState, type FormEvent } from "react";
import { ApiError, failureText, productionApi, type ProductionInstrument } from "./api";
import { formatNumber } from "./format";
import { PageWindow } from "./PageWindow";
import "./labelPrint.css";

const labelWord = (count: number) => count % 10 === 1 && count % 100 !== 11 ? "этикетку"
  : count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14) ? "этикетки" : "этикеток";

export function InstrumentLabelPrint({ instruments, bulk = false, close }: {
  instruments: ProductionInstrument[]; bulk?: boolean; close: () => void;
}) {
  const [copies, setCopies] = useState("1");
  const [online, setOnline] = useState<boolean | null>(null);
  const [qrLink, setQrLink] = useState<boolean | null>(null);
  const [qrWarning, setQrWarning] = useState("");
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<"loading" | "ready" | "failed">("loading");
  const [queued, setQueued] = useState<number | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const count = Number(copies) * instruments.length;
  const valid = Number.isInteger(Number(copies)) && Number(copies) >= 1 && Number(copies) <= 5;
  const first = instruments[0];

  useEffect(() => {
    let active = true;
    productionApi.instrumentLabelAgent().then((status) => { if (active) {
      setOnline(status.online); setQrLink(status.qr_link_enabled); setQrWarning(status.qr_warning ?? "");
    } })
      .catch((reason) => { if (active) setError(failureText(reason, "Не удалось проверить связь с принтером")); })
      .finally(() => { if (active) setChecking(false); });
    return () => { active = false; };
  }, []);

  async function refreshAgent() {
    setChecking(true); setError("");
    try {
      const status = await productionApi.instrumentLabelAgent();
      setOnline(status.online); setQrLink(status.qr_link_enabled); setQrWarning(status.qr_warning ?? "");
    }
    catch (reason) { setOnline(null); setError(failureText(reason, "Не удалось проверить связь с принтером")); }
    finally { setChecking(false); }
  }

  async function print(event: FormEvent) {
    event.preventDefault();
    if (busy || !first || !valid || count > 200 || !online || queued !== null || uncertain || preview !== "ready") return;
    setBusy(true); setError("");
    let submitted = false;
    try {
      const status = await productionApi.instrumentLabelAgent();
      setOnline(status.online);
      setQrLink(status.qr_link_enabled);
      setQrWarning(status.qr_warning ?? "");
      if (!status.online) return;
      submitted = true;
      if (bulk) await productionApi.printInstrumentLabels(instruments.map((item) => item.id), Number(copies));
      else await productionApi.printInstrumentLabel(first.id, Number(copies));
      setQueued(count);
    } catch (reason) {
      if (submitted && (!(reason instanceof ApiError) || reason.status >= 500)) setUncertain(true);
      setError(failureText(reason, submitted ? "Не удалось отправить этикетки на печать" : "Не удалось проверить связь с принтером"));
    } finally { setBusy(false); }
  }

  const requestClose = () => { if (!busy) close(); };
  return <PageWindow label="Печать этикеток" width={760} close={requestClose}>
    <div className="panel instrument-window">
      <header className="page-head"><h2>Печать этикеток</h2><button type="button" className="secondary small" disabled={busy} onClick={requestClose}>Закрыть</button></header>
      <p>{bulk ? `Записей: ${formatNumber(instruments.length)}` : `${first?.inventory_number ?? ""} · ${first?.name ?? ""}`}</p>
      <div className="label-print">
        {first && <img className="label-preview" src={productionApi.instrumentLabelUrl(first.id, first.version)}
          alt={`Предпросмотр этикетки ${first.inventory_number}`} onLoad={() => setPreview("ready")} onError={() => setPreview("failed")} />}
        {preview === "loading" && <p className="empty" role="status">Загрузка предпросмотра…</p>}
        {preview === "failed" && <div className="error-banner" role="alert">Не удалось загрузить предпросмотр этикетки</div>}
        {checking && <p className="empty" role="status">Проверка связи с принтером…</p>}
        {!checking && online === false && <div className="error-banner" role="alert">Принтер склада не на связи</div>}
        {error && <div className="error-banner" role="alert">{error}</div>}
        {qrLink === false && <p className="warning-inline" role="status">{qrWarning || "QR содержит только инвентарный номер. Публичная ссылка на паспорт не настроена."}</p>}
        {!checking && online !== true && <button className="secondary small" type="button" disabled={busy} onClick={() => void refreshAgent()}>Проверить связь</button>}
        {uncertain && <p className="warning-inline" role="status">Результат отправки неизвестен. Проверьте очередь принтера перед повторной печатью.</p>}
        {queued !== null && <div className="success-banner" role="status">Добавлено в очередь: {formatNumber(queued)} шт.</div>}
        <form className="label-print-form" onSubmit={(event) => void print(event)}>
          <label className="label-copies">Копий<input aria-label="Копий каждой этикетки" name="instrument_label_copies" type="number" autoComplete="off" min="1" max="5" step="1" value={copies}
            disabled={busy || queued !== null || uncertain} onChange={(event) => setCopies(event.target.value)} /></label>
          <button className="primary small" type="submit" disabled={busy || checking || online !== true || !first || !valid || count > 200 || queued !== null || uncertain || preview !== "ready"}>
            {busy ? "Отправка…" : `Печатать ${formatNumber(Number.isFinite(count) ? count : 0)} ${labelWord(count)}`}
          </button>
          <button className="secondary small" type="button" disabled={busy} onClick={requestClose}>{queued !== null ? "Готово" : "Отмена"}</button>
        </form>
      </div>
      <p role="status">Этикеток: {formatNumber(Number.isFinite(count) ? count : 0)}</p>
      {count > 200 && <div className="error-banner" role="alert">Не более 200 этикеток за один раз</div>}
    </div>
  </PageWindow>;
}
