import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, failureText } from "./api";
import { formatDate, formatMoment } from "./format";
import { PageWindow } from "./PageWindow";
import { StatusChip } from "./StatusChip";
import { equipmentSetupApi, type Ob019Context, type Ob019Key, type Ob019LiveCard, type Ob019Proposal, type SetupViewer } from "./equipmentSetupApi";

/* Наладка ОБ.019 (владелец 18.11.2026): наладчик сначала видит, что стоит в станке, затем правит. ERP предлагает
   последний рецепт станка для провода (иначе номинал ЭТК), показывает живые значения ПЛК рядом и пишет каждую правку
   в журнал; «Сохранить версию» делает фактические значения новой версией рецепта. В станок эта часть не пишет. */

const LIVE_MS = 3000;
type Mode = "job" | "manual";

export function mmNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const text = String(value).trim().replace(",", ".");
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}
/** Миллиметры как на пульте станка: два знака, запятая; пусто — «—». */
export function mm(value: string | number | null | undefined): string {
  const number = mmNumber(value);
  return number === null ? "—" : number.toFixed(2).replace(".", ",");
}
export function differs(a: string | number | null | undefined, b: string | number | null | undefined): boolean {
  const x = mmNumber(a), y = mmNumber(b);
  if (x === null || y === null) return false;
  return Math.abs(x - y) >= 0.005;
}
const toneOf = { ok: "success", warn: "warning", danger: "danger", info: "info", neutral: "neutral" } as const;

function useLive(machine: string) {
  const [card, setCard] = useState<Ob019LiveCard | null>(null), [error, setError] = useState("");
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined, controller: AbortController | null = null, stopped = false;
    const tick = async () => {
      if (stopped || document.visibilityState === "hidden") return;
      controller = new AbortController();
      let wait = LIVE_MS;
      try { setCard(await equipmentSetupApi.ob019Live(machine, controller.signal)); setError(""); }
      catch (reason) {
        if (controller.signal.aborted) return;
        // 404: моста станка в ERP ещё нет (или сервер без раздела «Станки») — спрашиваем реже.
        const absent = reason instanceof ApiError && reason.status === 404;
        if (absent) wait = LIVE_MS * 10;
        setCard(null); setError(absent ? "станок ещё не подключён к ERP" : failureText(reason, "нет связи с ERP"));
      }
      if (!stopped) timer = setTimeout(() => void tick(), wait);
    };
    const visible = () => { clearTimeout(timer); if (document.visibilityState === "hidden") controller?.abort(); else void tick(); };
    void tick();
    document.addEventListener("visibilitychange", visible);
    return () => { stopped = true; clearTimeout(timer); controller?.abort(); document.removeEventListener("visibilitychange", visible); };
  }, [machine]);
  return { card, error };
}

export function Ob019SetupPanel({ machineId, viewer, close }: { machineId: string; viewer: SetupViewer; close: () => void }) {
  const [context, setContext] = useState<Ob019Context | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [mode, setMode] = useState<Mode>("job"), [jobKey, setJobKey] = useState(""), [wire, setWire] = useState("");
  const [length, setLength] = useState(""), [quantity, setQuantity] = useState("");
  const [proposal, setProposal] = useState<Ob019Proposal | null>(null), [loading, setLoading] = useState(false);
  const [actual, setActual] = useState<Partial<Record<Ob019Key, string>>>({});
  const [journaled, setJournaled] = useState<Partial<Record<Ob019Key, string>>>({});
  const [savedAt, setSavedAt] = useState<Partial<Record<Ob019Key, string>>>({});
  const [busy, setBusy] = useState(false);
  const request = useRef(0);
  const { card, error: liveError } = useLive(machineId);
  const writable = viewer.writable;

  useEffect(() => {
    let alive = true;
    equipmentSetupApi.ob019Context(machineId).then(value => { if (alive) { setContext(value); if (!value.groups.length) setMode("manual"); } })
      .catch(reason => { if (alive) setError(failureText(reason, "Не удалось загрузить наладку ОБ.019")); });
    return () => { alive = false; };
  }, [machineId]);

  const job = context?.groups.find(item => item.key === jobKey) ?? null;
  const jobText = job ? `${job.number} · ${job.project} · ${job.wire} · ${mm(job.length_mm)} мм · ${job.quantity} шт` : "";
  const contextText = mode === "job" ? jobText : `вне проекта${quantity ? ` · ${quantity} шт` : ""}`;

  async function propose(query: { wire?: string; group_id?: string; length_mm?: string }) {
    const ticket = ++request.current;
    setLoading(true); setError(""); setNotice("");
    try {
      const value = await equipmentSetupApi.ob019Proposal(machineId, query);
      if (ticket !== request.current) return;
      const start = Object.fromEntries(value.values.map(item => [item.key, item.value === null ? "" : mm(item.value)])) as Partial<Record<Ob019Key, string>>;
      setProposal(value); setActual(start); setJournaled(start); setSavedAt({});
    } catch (reason) { if (ticket === request.current) { setProposal(null); setError(failureText(reason, "Не удалось получить предложение")); } }
    finally { if (ticket === request.current) setLoading(false); }
  }
  function pickJob(key: string) {
    setJobKey(key);
    const found = context?.groups.find(item => item.key === key);
    if (found) void propose({ group_id: found.group_id, length_mm: found.length_mm }); else setProposal(null);
  }
  function manual() {
    if (!wire) { setProposal(null); return; }
    void propose({ wire, length_mm: mmNumber(length) && mmNumber(length)! > 0 ? String(mmNumber(length)) : undefined });
  }

  const live = card?.live?.wire ?? null;
  const proposed = useMemo(() => Object.fromEntries((proposal?.values ?? []).map(item => [item.key, item])), [proposal]);

  async function journal(key: Ob019Key, value: string) {
    if (!writable || !proposal || !value.trim()) return;
    const number = mmNumber(value);
    if (number === null || number < 0) { setError(`${proposed[key]?.label ?? "Значение"}: нужно число не меньше нуля`); return; }
    const unchanged = mmNumber(journaled[key]) !== null && !differs(number, journaled[key] ?? null);
    if (unchanged) return;
    try {
      await equipmentSetupApi.ob019Correct(machineId, {
        parameter: key, actual: number.toFixed(2), proposed: proposed[key]?.value ?? null,
        live: live?.[key] === null || live?.[key] === undefined ? null : Number(live[key]).toFixed(2),
        wire: proposal.wire, context: contextText, quantity: mode === "manual" && mmNumber(quantity) ? Math.round(mmNumber(quantity)!) : null,
      });
      setJournaled(values => ({ ...values, [key]: mm(number) }));
      setSavedAt(values => ({ ...values, [key]: new Date().toISOString() }));
      setActual(values => ({ ...values, [key]: mm(number) }));
    } catch (reason) { setError(failureText(reason, "Правка не записана в журнал")); }
  }
  async function takeFromMachine() {
    if (!live || busy) return;
    setBusy(true);
    try {
      for (const parameter of context?.parameters ?? []) {
        const value = live[parameter.key];
        if (value === null || value === undefined) continue;
        if (mmNumber(actual[parameter.key]) === null || differs(value, actual[parameter.key] ?? null)) await journal(parameter.key, String(value));
      }
    } finally { setBusy(false); }
  }
  async function saveVersion() {
    if (!proposal || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const values = Object.fromEntries((context?.parameters ?? []).filter(item => item.in_recipe).map(item => [item.key, String(mmNumber(actual[item.key]) ?? "")]));
      const saved = await equipmentSetupApi.ob019SaveVersion(machineId, {
        ...proposal.publish, wire: proposal.wire, values, context: contextText,
        etk_strip_front_mm: proposal.etk?.strip_front_mm ?? null, etk_strip_back_mm: proposal.etk?.strip_back_mm ?? null,
      });
      setNotice(`Версия ${saved.version} сохранена: ${saved.title}. В следующий раз предложение начнётся с неё.`);
      setProposal(value => value ? { ...value, recipe: { recipe_id: saved.recipe_id, version: saved.version, title: saved.title, created_at: saved.created_at, source: "" }, publish: { recipe_id: saved.recipe_id, expected_version: saved.version } } : value);
    } catch (reason) { setError(failureText(reason, "Не удалось сохранить версию")); }
    finally { setBusy(false); }
  }

  const missing = (context?.parameters ?? []).filter(item => item.in_recipe && mmNumber(actual[item.key]) === null);
  const stale = card && card.link.code !== "ok";
  const source = !proposal ? "" : proposal.recipe ? `рецепт «${proposal.recipe.title}», версия ${proposal.recipe.version} от ${formatDate(proposal.recipe.created_at)}`
    : proposal.etk ? "номинал ЭТК — рецепта для этого провода ещё нет" : "рецепта для этого провода ещё нет";

  return <PageWindow label="Наладка ОБ.019" close={() => { if (!busy) close(); }} width={1160}><div className="panel equipment-setup ob019-setup">
    <div className="page-head"><h2>{context?.machine.inventory_number || "ОБ.019"} · наладка резки и зачистки</h2><button className="secondary" disabled={busy} onClick={close}>Закрыть</button></div>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {notice && <p className="success-banner" role="status">{notice}</p>}
    {!context ? !error && <p className="empty">Загрузка наладки…</p> : <>
      <div className="ob019-bar">
        <div className="seg-tabs" role="radiogroup" aria-label="Откуда задание">
          {([["job", "По заданию резки"], ["manual", "Вне проекта"]] as Array<[Mode, string]>).map(([value, label]) =>
            <button key={value} type="button" role="radio" aria-checked={mode === value} className={mode === value ? "on" : undefined}
              onClick={() => { if (mode !== value) { setMode(value); setProposal(null); setJobKey(""); } }}>{label}</button>)}
        </div>
        <div className="ob019-live" role="status" aria-live="polite">
          {card ? <><StatusChip tone={toneOf[card.link.tone]}>{card.link.text}</StatusChip>
            {card.live && <span>{card.live.state.text}</span>}
            <span className="muted">{card.reported_at ? `сигнал ${formatMoment(card.reported_at)}` : "сигнала ещё не было"}</span></>
            : <span className="muted">{liveError ? `Живые значения недоступны: ${liveError}` : "Связь со станком…"}</span>}
        </div>
      </div>
      {mode === "job" ? <div className="setup-grid ob019-pick">
        <label className="setup-wide">Задание резки (группа · проект · провод · длина · количество)
          <select name="job" value={jobKey} disabled={loading} onChange={event => pickJob(event.target.value)}>
            <option value="">{context.groups.length ? "Выберите задание" : "Нет открытых групп резки провода на площадке"}</option>
            {context.groups.map(item => <option key={item.key} value={item.key}>{`${item.number} · ${item.project} · ${item.wire} · ${mm(item.length_mm)} мм · ${item.quantity} шт`}</option>)}
          </select></label>
      </div> : <form className="setup-grid ob019-pick" onSubmit={event => { event.preventDefault(); manual(); }}>
        <label>Провод<select name="wire" value={wire} disabled={loading} onChange={event => setWire(event.target.value)}>
          <option value="">Выберите провод</option>
          {context.wires.map(item => <option key={item.label} value={item.label}>{item.label}{item.has_recipe ? " · есть рецепт" : ""}</option>)}
        </select></label>
        <label>Длина провода, мм<input name="length" inputMode="decimal" autoComplete="off" value={length} disabled={loading} onChange={event => setLength(event.target.value)} /></label>
        <label>Количество, шт<input name="quantity" inputMode="numeric" autoComplete="off" value={quantity} disabled={loading} onChange={event => setQuantity(event.target.value.replace(/\D/g, ""))} /></label>
        <div className="setup-actions ob019-pick-action"><button className="primary" disabled={!wire || loading}>Показать предложение</button></div>
      </form>}
      {loading && <p className="empty">Загрузка предложения…</p>}
      {proposal && !loading && <section>
        <p className="ob019-source"><strong>{proposal.wire}</strong> · предложение: {source}{stale ? " · значения станка устарели" : ""}</p>
        <div className="table-wrap"><table className="ob019-table">
          <thead><tr><th scope="col">Параметр</th><th scope="col">Регистр</th><th scope="col" className="num">Предложение, мм</th><th scope="col">Откуда</th>
            <th scope="col" className="num">В станке, мм</th><th scope="col" className="num">Фактически, мм</th><th scope="col">Журнал</th></tr></thead>
          <tbody>{(context.parameters).map(parameter => {
            const offer = proposed[parameter.key], machine = live?.[parameter.key] ?? null;
            const off = differs(machine, offer?.value ?? null), changed = differs(actual[parameter.key] ?? null, offer?.value ?? null);
            return <tr key={parameter.key} className={off || changed ? "ob019-diff" : undefined}>
              <th scope="row">{parameter.label}</th>
              <td className="muted mono">{parameter.register}</td>
              <td className="num">{mm(offer?.value)}</td>
              <td className={offer?.value === null ? "tone-warn" : "muted"}>{offer?.source}</td>
              <td className={`num${off ? " tone-warn" : ""}${stale ? " muted" : ""}`} title={off ? "Отличается от предложения" : undefined}>{off ? "≠ " : ""}{mm(machine)}</td>
              <td className="num"><input aria-label={`${parameter.label}, фактически`} name={parameter.key} inputMode="decimal" autoComplete="off"
                className={changed ? "ob019-changed" : undefined} disabled={!writable || busy} value={actual[parameter.key] ?? ""}
                onChange={event => setActual(values => ({ ...values, [parameter.key]: event.target.value }))}
                onBlur={event => void journal(parameter.key, event.target.value)}
                onKeyDown={event => { if (event.key === "Enter") void journal(parameter.key, (event.target as HTMLInputElement).value); }} /></td>
              <td className="muted">{savedAt[parameter.key] ? `правка записана ${formatMoment(savedAt[parameter.key]!)}` : "—"}</td>
            </tr>;
          })}</tbody>
        </table></div>
        <p className="muted ob019-hint">≠ — значение станка отличается от предложения. Значения в станок пока вводятся на его пульте.</p>
        <div className="setup-actions">
          {writable && <button className="secondary" disabled={busy || !live} onClick={() => void takeFromMachine()}>Взять значения со станка</button>}
          {writable && viewer.can_assign && <button className="primary" disabled={busy || missing.length > 0} title={missing.length ? `Не заданы: ${missing.map(item => item.label).join(", ")}` : undefined}
            onClick={() => void saveVersion()}>{busy ? "Сохраняю…" : `Сохранить версию ${proposal.publish.expected_version + 1}`}</button>}
          {writable && !viewer.can_assign && <span className="muted">Версию рецепта сохраняет мастер площадки</span>}
        </div>
      </section>}
    </>}
  </div></PageWindow>;
}
