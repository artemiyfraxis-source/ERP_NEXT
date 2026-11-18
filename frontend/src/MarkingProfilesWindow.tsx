import { useEffect, useRef, useState } from "react";
import { ApiError, failureText } from "./api";
import { DataTable } from "./DataTable";
import { PageWindow } from "./PageWindow";
import { formatNumber } from "./format";
import { useCanPrint, useCurrentUserId } from "./rights";
import { boundPrinter, markingApi, type MarkingCatalog, type MarkingGeometry, type MarkingProfile, type MarkingRaster, type MarkingRect } from "./markingApi";
import { MarkingBatchProgress } from "./MarkingPrintWindow";
import "./marking.css";

/** Cloned PCs share one hostname: the IP and a short machine number tell them apart. */
function workplaceName(printer: { pc: string; machine_id: string; ip?: string }) {
  const details = [printer.ip, `№ ${printer.machine_id.slice(0, 8)}`].filter(Boolean).join(", ");
  return `${printer.pc || "ПК без имени"} (${details})`;
}

export function MarkingProfilesWindow({ equipmentIds, canEdit, close }: { equipmentIds: string[]; canEdit: boolean; close: () => void }) {
  const [catalog, setCatalog] = useState<MarkingCatalog | null>(null);
  const [machineId, setMachineId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [editorBusy, setEditorBusy] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);
  const ids = JSON.stringify(equipmentIds);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    markingApi.machines(controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      const allowed = new Set<string>(JSON.parse(ids));
      const machines = result.machines.filter((machine) => allowed.has(machine.equipment_id));
      setCatalog({ ...result, machines });
      if (machines.length === 1) { setMachineId(machines[0].equipment_id); setProfileId(machines[0].profiles[0]?.id ?? ""); }
    }).catch((problem) => { if (!controller.signal.aborted) setError(failureText(problem, "Не удалось загрузить профили")); });
    return () => controller.abort();
  }, [ids, reload]);
  const machine = catalog?.machines.find((item) => item.equipment_id === machineId);
  const profile = machine?.profiles.find((item) => item.id === profileId);
  const printers = catalog?.printers.filter((item) => item.kind === "gdi_png" && item.present) ?? [];
  const printer = catalog ? boundPrinter(catalog, machine) : undefined;
  const locked = busy || editorBusy || editorDirty;
  async function bind() {
    if (busy || !machine || !target) return;
    const chosen = printers.find((item) => JSON.stringify([item.machine_id, item.name]) === target);
    if (!chosen) return;
    setBusy(true); setError("");
    try {
      const binding = await markingApi.bind(machine.equipment_id, { version: machine.binding?.version ?? 0, machine_id: chosen.machine_id, printer: chosen.name });
      setCatalog((current) => current && { ...current, machines: current.machines.map((item) => item.equipment_id === machine.equipment_id ? { ...item, binding } : item) });
      setTarget("");
    } catch (problem) { setError(failureText(problem, "Принтер не привязан")); }
    finally { setBusy(false); }
  }
  return <PageWindow label="Профили маркировки" width={1180} close={() => { if (!locked) close(); }}>
    <div className="panel marking-window">
      <div className="marking-head"><h2>Профили маркировки</h2><div className="form-actions">
        {canEdit && catalog && <button type="button" className="secondary" disabled={locked} onClick={() => { setCatalog(null); setProfileId(""); setTarget(""); setReload(reload + 1); }}>Обновить профили</button>}
        <button type="button" className="secondary" disabled={locked} onClick={close}>Закрыть</button></div></div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!catalog && !error && <div className="empty" role="status">Загрузка…</div>}
      {catalog && !catalog.machines.length && <div className="empty">Профилей для этих экземпляров нет — проверьте инвентарные номера ОБ.101 и ОБ.102.</div>}
      {catalog && catalog.machines.length > 0 && <>
        <div className="marking-controls">
          <label>Станок<select name="profile-machine" autoComplete="off" disabled={locked} value={machineId} onChange={(event) => { setMachineId(event.target.value); setProfileId(""); setTarget(""); }}>
            <option value="">Выберите станок…</option>{catalog.machines.map((item) => <option key={item.equipment_id} value={item.equipment_id}>{item.inventory_number} · {item.name}</option>)}</select></label>
          <label>Материал / профиль<select name="equipment-profile" autoComplete="off" disabled={locked || !machine} value={profileId} onChange={(event) => setProfileId(event.target.value)}>
            <option value="">Выберите профиль…</option>{machine?.profiles.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.calibrated ? "Откалиброван" : "Не откалиброван"}</option>)}</select></label>
          <div className="marking-target"><span className="muted">Текущий принтер</span><b>{machine?.binding?.printer || "Не привязан"}</b>
            {machine?.binding && <span className={printer?.online ? "tone-ok" : "tone-warn"}>{printer ? `${workplaceName(printer)} · ` : ""}{printer?.online ? "На связи" : "Не на связи"}</span>}</div>
        </div>
        {canEdit && machine && <div className="marking-binding">
          <label>Принтер на рабочем месте<select name="equipment-printer" autoComplete="off" disabled={locked} value={target} onChange={(event) => setTarget(event.target.value)}>
            <option value="">Выберите принтер…</option>{printers.map((item) => <option key={JSON.stringify([item.machine_id, item.name])} value={JSON.stringify([item.machine_id, item.name])}>{workplaceName(item)} · {item.name}{item.online ? "" : " · Не на связи"}</option>)}</select></label>
          <button type="button" className="secondary" disabled={locked || !target} onClick={() => void bind()}>{busy ? "Сохранение…" : "Привязать принтер"}</button>
        </div>}
        {profile && <ProfileEditor key={profile.id} profile={profile} canEdit={canEdit} online={Boolean(printer?.online && printer.present)} onBusy={setEditorBusy} onDirty={setEditorDirty}
          onSaved={(updated) => setCatalog((current) => current && { ...current, machines: current.machines.map((item) => ({ ...item, profiles: item.profiles.map((row) => row.id === updated.id ? updated : row) })) })} />}
      </>}
      {!catalog && error && <button type="button" className="secondary" onClick={() => setReload(reload + 1)}>Повторить загрузку</button>}
    </div>
  </PageWindow>;
}

function ProfileEditor({ profile, canEdit, online, onSaved, onBusy, onDirty }: {
  profile: MarkingProfile; canEdit: boolean; online: boolean; onSaved: (profile: MarkingProfile) => void; onBusy: (busy: boolean) => void; onDirty: (dirty: boolean) => void;
}) {
  const userId = useCurrentUserId();
  const storageName = `marking-calibration:${userId ?? "local"}:${profile.id}`;
  type CalibrationRecord = { text: string; version: number; key: string; pending: boolean; batchId?: string };
  const [restored] = useState(() => {
    try { return { record: JSON.parse(sessionStorage.getItem(storageName) || "null") as CalibrationRecord | null, error: "" }; }
    catch { return { record: null, error: "Не удалось восстановить пробную печать — проверьте хранилище браузера" }; }
  });
  const [calibrationIntent, setCalibrationIntent] = useState(restored.record);
  const [geometry, setGeometry] = useState(profile.geometry);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(restored.error);
  const [notice, setNotice] = useState("");
  const [text, setText] = useState(restored.record?.text ?? "АБВ 123");
  const [preview, setPreview] = useState<MarkingRaster | null>(null);
  const [batchId, setBatchId] = useState(restored.record?.batchId ?? "");
  const [confirmed, setConfirmed] = useState(false);
  const inFlight = useRef(false);
  const canPrint = useCanPrint();
  const dirty = JSON.stringify(geometry) !== JSON.stringify(profile.geometry);
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  useEffect(() => { setGeometry(profile.geometry); setPreview(null); setConfirmed(false); }, [profile]);
  useEffect(() => {
    if (!dirty) return;
    const prevent = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty]);
  const valid = Object.entries(geometry).every(([, value]) => typeof value !== "number" || Number.isFinite(value))
    && [...geometry.zones, ...geometry.blank_zones].every((zone) => Object.values(zone).every(Number.isFinite));
  const disabled = busy || !canEdit || Boolean(calibrationIntent?.pending);
  function change<K extends keyof MarkingGeometry>(key: K, value: MarkingGeometry[K]) { setGeometry((current) => ({ ...current, [key]: value })); setPreview(null); setConfirmed(false); setNotice(""); }
  async function act(action: () => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); onBusy(true); setError(""); setNotice("");
    try { await action(); }
    catch (problem) { setError(failureText(problem, "Действие не выполнено")); }
    finally { inFlight.current = false; setBusy(false); onBusy(false); }
  }
  async function save(calibrated: boolean) {
    await act(async () => { onSaved(await markingApi.profile(profile, geometry, calibrated)); setNotice(calibrated ? "Калибровка подтверждена" : "Профиль сохранён — выполните пробную печать"); });
  }
  async function calibrate(print: boolean) {
    await act(async () => {
      const request: CalibrationRecord = calibrationIntent?.pending ? calibrationIntent : { text, version: profile.version, key: crypto.randomUUID(), pending: true };
      if (print) {
        try { sessionStorage.setItem(storageName, JSON.stringify(request)); }
        catch { throw new Error("Не удалось сохранить запрос пробной печати — проверьте хранилище браузера"); }
        setCalibrationIntent(request);
      }
      let result;
      try { result = await markingApi.calibration({ ...profile, version: print ? request.version : profile.version }, print ? request.text : text, print, print ? request.key : undefined); }
      catch (problem) {
        if (print && problem instanceof ApiError && problem.status >= 400 && problem.status < 500) {
          sessionStorage.removeItem(storageName); setCalibrationIntent(null);
        }
        throw problem;
      }
      setPreview(result);
      if (result.batch_id) {
        const completed = { ...request, pending: false, batchId: result.batch_id };
        try { sessionStorage.setItem(storageName, JSON.stringify(completed)); }
        catch { throw new Error("Пробная этикетка поставлена в очередь, но её состояние не сохранено — повторите запрос пробы"); }
        setCalibrationIntent(completed);
        setBatchId(result.batch_id); setNotice("Пробная этикетка поставлена в очередь");
      }
    });
  }
  const number = (key: keyof Pick<MarkingGeometry, "width_mm" | "height_mm" | "offset_x_mm" | "offset_y_mm" | "min_font_mm" | "max_font_mm" | "max_lines">, label: string, min: number, max: number, step = "0.1") =>
    <label>{label}<input type="number" name={`geometry-${key}`} autoComplete="off" inputMode="decimal" min={min} max={max} step={step} disabled={disabled}
      value={Number.isFinite(geometry[key]) ? geometry[key] : ""} onChange={(event) => change(key, event.target.valueAsNumber)} /></label>;
  function rectangles(key: "zones" | "blank_zones", label: string) {
    return <DataTable rows={geometry[key].map((zone, index) => ({ ...zone, index }))} rowKey={(zone) => String(zone.index)} label={label} compactBelow={Infinity} phoneCards={false} columns={[
      { key: "zone", title: label, value: (zone) => `${key === "zones" ? "Текст" : "Пустая зона"} ${zone.index + 1}` },
      ...([ ["x", "Слева, мм"], ["y", "От начала подачи, мм"], ["width", "Ширина, мм"], ["height", "Высота, мм"] ] as const).map(([field, title]) => ({
        key: field, title, numeric: true, value: (zone: MarkingRect) => zone[field], render: (zone: MarkingRect & { index: number }) =>
          <input type="number" name={`${key}-${zone.index}-${field}`} autoComplete="off" inputMode="decimal" min={field === "x" || field === "y" ? 0 : 0.1} max={150} step="0.1"
            aria-label={`${label} ${zone.index + 1}: ${title}`} disabled={disabled} value={Number.isFinite(zone[field]) ? zone[field] : ""}
            onChange={(event) => change(key, geometry[key].map((row, index) => index === zone.index ? { ...row, [field]: event.target.valueAsNumber } : row))} />,
      })),
    ]} />;
  }
  return <section className="marking-profile-editor" aria-label="Геометрия профиля">
    <div className="marking-head"><h3>Размеры и расположение текста</h3><span className={profile.calibrated && !dirty ? "tone-ok" : "tone-warn"}>{profile.calibrated && !dirty ? "Откалиброван" : "Не откалиброван"}</span></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {calibrationIntent?.pending && <p className="warning-inline">Проверьте постановку предыдущей пробной этикетки: повторите запрос пробы.</p>}
    {notice && <p className="success-banner" role="status">{notice}</p>}
    <div className="marking-profile-layout"><div className="marking-profile-dimensions">
    <div className="marking-geometry">
      {number("width_mm", "Ширина этикетки, мм", 5, 150)}{number("height_mm", "Высота этикетки, мм", 5, 150)}
      <label>Поворот<select name="geometry-rotation" autoComplete="off" disabled={disabled} value={geometry.rotation} onChange={(event) => change("rotation", Number(event.target.value) as MarkingGeometry["rotation"])}>
        {[0, 90, 180, 270].map((value) => <option key={value} value={value}>{value}°</option>)}</select></label>
      <label>Подача<select name="geometry-feed" autoComplete="off" disabled={disabled} value={geometry.feed_direction} onChange={(event) => change("feed_direction", event.target.value as MarkingGeometry["feed_direction"])}>
        <option value="height">Вдоль высоты</option><option value="width">Вдоль ширины</option></select></label>
      {number("offset_x_mm", "Смещение по ширине, мм", -5, 5)}{number("offset_y_mm", "Смещение по подаче, мм", -5, 5)}
      {number("min_font_mm", "Минимальный шрифт, мм", 1.6, 10)}{number("max_font_mm", "Максимальный шрифт, мм", 1.6, 15)}
      {number("max_lines", "Не более строк", 1, 3, "1")}
      <label>Выравнивание<select name="geometry-alignment" autoComplete="off" disabled={disabled} value={geometry.alignment} onChange={(event) => change("alignment", event.target.value as MarkingGeometry["alignment"])}>
        <option value="center">По центру</option><option value="left">По левому краю</option><option value="right">По правому краю</option></select></label>
    </div>
    {rectangles("zones", "Текстовые зоны")}{rectangles("blank_zones", "Зоны без печати")}
    {canEdit && <div className="form-actions"><button type="button" className="secondary" disabled={busy || !dirty || !valid} onClick={() => void save(false)}>Сохранить размеры</button>
      {dirty && <><button type="button" className="secondary" disabled={busy} onClick={() => { setGeometry(profile.geometry); setError(""); }}>Отменить изменения</button>
        <span className="muted">Сохраните или отмените изменения перед закрытием.</span></>}</div>}
    </div><div className="marking-calibration">
      <h3>Пробная печать</h3>
      <label>Текст пробной этикетки<textarea name="marking-calibration-text" autoComplete="off" maxLength={1000} disabled={disabled} value={text}
        onChange={(event) => { setText(event.target.value); setPreview(null); setConfirmed(false); }} /></label>
      {dirty && <p className="warning-inline">Сохраните размеры перед пробной печатью.</p>}
      <div className="form-actions"><button type="button" className="secondary" disabled={disabled || dirty || !text.trim()} onClick={() => void calibrate(false)}>Показать пробную этикетку</button>
        {canPrint && <button type="button" className="primary" disabled={busy || !canEdit || Boolean(restored.error) || dirty || !text.trim() || (!online && !calibrationIntent?.pending)} onClick={() => void calibrate(true)}>{busy ? "Обработка…" : calibrationIntent?.pending ? "Повторить запрос пробы" : "Пробная печать"}</button>}</div>
      {preview && <figure className="marking-raster"><figcaption>Пробная этикетка · <span className="tnum">{formatNumber(preview.width_mm)} × {formatNumber(preview.height_mm)} мм</span></figcaption>
        <img src={`data:image/png;base64,${preview.png_b64}`} alt={`Пробная этикетка: ${text}`}
          width={Math.round(preview.width_mm * preview.dpi / 25.4)} height={Math.round(preview.height_mm * preview.dpi / 25.4)} /></figure>}
      {canEdit && !profile.calibrated && <><label className="check-inline"><input type="checkbox" name="marking-calibration-confirm" checked={confirmed} disabled={busy || dirty}
        onChange={(event) => setConfirmed(event.target.checked)} />Размер, положение текста и подача проверены на станке</label>
        <button type="button" className="secondary" disabled={disabled || dirty || !confirmed} onClick={() => void save(true)}>Подтвердить калибровку</button></>}
      {batchId && <CalibrationProgress key={batchId} id={batchId} />}
    </div></div>
  </section>;
}

function CalibrationProgress({ id }: { id: string }) {
  const [batch, setBatch] = useState<Awaited<ReturnType<typeof markingApi.batch>> | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    markingApi.batch(id, controller.signal).then((result) => { if (!controller.signal.aborted) { setBatch(result); setError(""); } })
      .catch((problem) => { if (!controller.signal.aborted) setError(failureText(problem, "Не удалось получить состояние пробной печати")); });
    return () => controller.abort();
  }, [id, reload]);
  return batch ? <MarkingBatchProgress initial={batch} /> : error ? <div className="error-banner" role="alert">{error} <button type="button" className="secondary" onClick={() => setReload(reload + 1)}>Обновить состояние</button></div>
    : <div className="empty" role="status">Загрузка очереди…</div>;
}
