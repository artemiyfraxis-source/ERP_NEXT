import { useEffect, useRef, useState } from "react";
import { ApiError, failureText, rfInterfacesApi, type InstrumentRfDetails, type RfInterface } from "./api";
import { formatNumber } from "./format";

const roles: Record<string, string> = {
  unknown: "Не указано", vna: "ВАЦ", adapter: "Переходник", test_cable: "Тестовый кабель",
  auto_calibration: "АКМ", solt_kit: "Набор SOLT", load: "Нагрузка", load_kit: "Нагрузки SMA-MF06",
};
const series = ["SMA", "3.5mm", "N", "TNC", "BNC", "F", "RP-SMA", "тип III"];
const blankPort = (): RfInterface => ({ series: "", body: "", center: "", impedance_ohm: null, f_max_ghz: null, f_confirmed: false, f_source: "" });
const number = (value: string) => value === "" ? null : Number(value);

export function InstrumentRfInterfaces({ instrumentId, locked, onDirty, onBusy }: {
  instrumentId: string | undefined; locked: boolean; onDirty: (value: boolean) => void; onBusy: (value: boolean) => void;
}) {
  const [saved, setSaved] = useState<InstrumentRfDetails | null>(null);
  const [draft, setDraft] = useState<InstrumentRfDetails | null>(null);
  const [loading, setLoading] = useState(Boolean(instrumentId));
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const form = useRef<HTMLFormElement>(null);
  const inFlight = useRef(false);
  const dirty = Boolean(draft && JSON.stringify(draft) !== JSON.stringify(saved));

  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  useEffect(() => {
    if (!instrumentId) return;
    let active = true;
    setLoading(true); setError("");
    rfInterfacesApi.get(instrumentId).then(({ rf }) => {
      if (!rf) throw new Error("ВЧ-интерфейсы не получены");
      if (active) { setSaved(rf); setDraft(rf); setConflict(false); }
    }).catch((reason) => { if (active) setError(failureText(reason, "Не удалось загрузить ВЧ-интерфейсы")); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [instrumentId, attempt]);

  function change(values: Partial<InstrumentRfDetails>) {
    setDraft((current) => current ? { ...current, ...values, confirmed: false,
      interfaces: (values.interfaces ?? current.interfaces).map((port) => ({ ...port, f_confirmed: false })) } : current);
  }
  function portChange(index: number, values: Partial<RfInterface>) {
    if (draft) change({ interfaces: draft.interfaces.map((port, i) => i === index ? { ...port, ...values } : port) });
  }
  async function save(confirm: boolean) {
    if (!instrumentId || !draft || locked || inFlight.current || conflict || !form.current?.reportValidity()) return;
    inFlight.current = true; setBusy(true); onBusy(true); setError(""); setNotice("");
    try {
      const payload = { ...draft, confirmed: confirm, interfaces: draft.interfaces.map((port) => ({
        ...port, f_confirmed: confirm && port.f_max_ghz !== null && port.impedance_ohm !== null,
        f_source: port.f_source.trim() || draft.source.trim(),
      })) };
      const { rf } = await rfInterfacesApi.save(instrumentId, payload);
      setSaved(rf); setDraft(rf); setEditing(false);
      setNotice(confirm ? "ВЧ-интерфейсы подтверждены" : "ВЧ-интерфейсы сохранены без подтверждения");
    } catch (reason) {
      setError(failureText(reason, "Не удалось сохранить ВЧ-интерфейсы"));
      if (reason instanceof ApiError && reason.status === 409) setConflict(true);
    } finally { inFlight.current = false; setBusy(false); onBusy(false); }
  }

  return <section className="instrument-rf" aria-label="ВЧ-интерфейсы" aria-busy={loading || busy}>
    <div className="instrument-verification-head"><h3>ВЧ-интерфейсы</h3>
      {!editing && saved && <button type="button" className="secondary small" disabled={locked || loading} onClick={() => { setEditing(true); setNotice(""); }}>
        {saved.interfaces.length ? "Изменить интерфейсы" : "Добавить интерфейсы"}
      </button>}
    </div>
    {!instrumentId && <p className="muted-text">Сохраните прибор, чтобы указать ВЧ-интерфейсы.</p>}
    {loading && <p className="empty">Загрузка ВЧ-интерфейсов…</p>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner" role="status">{notice}</div>}
    {error && !loading && <button type="button" className="secondary small" disabled={locked || busy} onClick={() => {
      if (dirty && !window.confirm("Обновить ВЧ-интерфейсы и заменить несохранённые изменения?")) return;
      setAttempt((value) => value + 1);
    }}>Обновить ВЧ-интерфейсы</button>}
    {saved && !editing && !loading && <>
      <p className={saved.confirmed ? "tone-ok" : "warning-inline"}>{saved.confirmed ? "Подтверждены" : "Не подтверждены"} · {roles[saved.role] ?? "Не указано"}</p>
      {saved.interfaces.length ? saved.interfaces.map((port, i) => <p key={i}>
        {saved.role === "vna" || saved.role === "auto_calibration" ? "Порт" : "Интерфейс"} {i + 1}: {port.series} {port.body}, {port.center} · {port.impedance_ohm == null ? "сопротивление не указано" : `${formatNumber(port.impedance_ohm)} Ом`} · {port.f_max_ghz == null ? "частота не указана" : `до ${formatNumber(port.f_max_ghz, { digits: 9 })} ГГц`}
      </p>) : <p className="muted-text">Интерфейсы не указаны — добавьте данные из паспорта.</p>}
      {saved.interfaces.length > 0 && <p>Частота от: {saved.f_min_ghz === null ? "не указана" : `${formatNumber(saved.f_min_ghz, { digits: 9 })} ГГц`}
        {saved.role === "vna" && <> · Максимум точек: {saved.max_points === null ? "не указан" : formatNumber(saved.max_points)}</>}
      </p>}
      {saved.source && <p className="muted-text">Источник: {saved.source}</p>}
      {saved.confirmed && (saved.f_min_ghz === null || saved.interfaces.some((port) => !port.f_confirmed)) && <p className="warning-inline">Разъёмы подтверждены; рабочую полосу нужно уточнить.</p>}
    </>}
    {editing && draft && !loading && <form ref={form} onSubmit={(event) => { event.preventDefault(); void save(false); }}>
      <fieldset className="instrument-fields" disabled={locked || busy}>
        <div className="form-grid">
          <label>Назначение<select name="rf_role" value={draft.role} onChange={(event) => change({ role: event.target.value, max_points: null, kit_definition: "" })}>
            {Object.entries(roles).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select></label>
          <label>Частота от, ГГц<input name="rf_min" type="number" min="0" max="1000" step="any" value={draft.f_min_ghz ?? ""} onChange={(event) => change({ f_min_ghz: number(event.target.value) })} /></label>
          {draft.role === "vna" && <label>Максимум точек ВАЦ<input name="rf_points" type="number" min="2" max="10000001" step="1" value={draft.max_points ?? ""} onChange={(event) => change({ max_points: number(event.target.value) })} /></label>}
          <label className="wide">Источник данных<input name="rf_source" autoComplete="off" maxLength={2000} value={draft.source} onChange={(event) => change({ source: event.target.value })} /></label>
          {draft.role === "solt_kit" && <label className="wide">Состав набора<select name="rf_kit" value={draft.kit_definition} onChange={(event) => change({ kit_definition: event.target.value })}>
            <option value="">Не подтверждён</option><option value="6650F27-full">6650F27: холостой ход, КЗ, нагрузка — вилка и розетка; проходы MA6666A/B/C-11</option>
          </select></label>}
          {draft.role === "load_kit" && <label className="wide">Состав нагрузок<select name="rf_load_kit" value={draft.kit_definition} onChange={(event) => change({ kit_definition: event.target.value })}>
            <option value="">Не подтверждён</option><option value="SMA-MF06-loads">Нагрузки 50 Ом из SMA-MF06</option>
          </select></label>}
        </div>
        {draft.interfaces.map((port, i) => <div key={i}>
          <div className="instrument-verification-head"><h4>{draft.role === "vna" || draft.role === "auto_calibration" ? "Порт" : "Интерфейс"} {i + 1}</h4>
            <button type="button" className="secondary small" aria-label={`Удалить интерфейс ${i + 1}`} onClick={() => change({ interfaces: draft.interfaces.filter((_, index) => index !== i) })}>Удалить</button>
          </div>
          <div className="form-grid">
            <label>Серия {i + 1}<select name={`rf_series_${i}`} required value={port.series} onChange={(event) => portChange(i, { series: event.target.value })}>
              <option value="">Не указана</option>{series.map((value) => <option key={value}>{value}</option>)}
            </select></label>
            <label>Корпус {i + 1}<select name={`rf_body_${i}`} required value={port.body} onChange={(event) => portChange(i, { body: event.target.value })}>
              <option value="">Не указан</option><option>вилка</option><option>розетка</option>
            </select></label>
            <label>Центральный контакт {i + 1}<select name={`rf_center_${i}`} required value={port.center} onChange={(event) => portChange(i, { center: event.target.value })}>
              <option value="">Не указан</option><option>штырь</option><option>гнездо</option>
            </select></label>
            <label>Сопротивление {i + 1}, Ом<input name={`rf_ohms_${i}`} type="number" min="0.001" max="1000" step="any" value={port.impedance_ohm ?? ""} onChange={(event) => portChange(i, { impedance_ohm: number(event.target.value) })} /></label>
            <label>Частота до {i + 1}, ГГц<input name={`rf_max_${i}`} type="number" min="0.000001" max="1000" step="any" value={port.f_max_ghz ?? ""} onChange={(event) => portChange(i, { f_max_ghz: number(event.target.value) })} /></label>
            <label className="wide">Источник интерфейса {i + 1}<input name={`rf_source_${i}`} autoComplete="off" maxLength={2000} value={port.f_source} onChange={(event) => portChange(i, { f_source: event.target.value })} /></label>
          </div>
        </div>)}
        <div className="form-actions">
          {draft.interfaces.length < 2 && <button type="button" className="secondary small" onClick={() => change({ interfaces: [...draft.interfaces, blankPort()] })}>Добавить интерфейс</button>}
          <button type="button" className="secondary small" onClick={() => { setDraft(saved); setEditing(false); setError(""); }}>Отменить</button>
          <button type="submit" className="secondary small" disabled={conflict}>Сохранить без подтверждения</button>
          <button type="button" className="primary small" disabled={conflict} onClick={() => void save(true)}>Подтвердить</button>
        </div>
      </fieldset>
    </form>}
  </section>;
}
