import { useEffect, useRef, useState } from "react";
import { ApiError, failureText } from "./api";
import { DataTable } from "./DataTable";
import { PageWindow } from "./PageWindow";
import { formatDateTime, formatNumber } from "./format";
import { useCanAct, useCanPrint, useCurrentUserId } from "./rights";
import { boundPrinter, joinHalves, markingApi, splitHalves, type MarkingCatalog, type MarkingGeometry, type MarkingMachine, type MarkingPrinterCommand, type MarkingProfile, type MarkingRaster, type MarkingRect } from "./markingApi";
import { MarkingBatchProgress } from "./MarkingPrintWindow";
import "./marking.css";

/** Cloned PCs share one hostname: the IP and a short machine number tell them apart. */
function workplaceName(printer: { pc: string; machine_id: string; ip?: string }) {
  const details = [printer.ip, `№ ${printer.machine_id.slice(0, 8)}`].filter(Boolean).join(", ");
  return `${printer.pc || "ПК без имени"} (${details})`;
}

export function MarkingProfilesWindow({ equipmentIds, canEdit, close }: { equipmentIds: string[]; canEdit: boolean; close: () => void }) {
  const [catalog, setCatalog] = useState<MarkingCatalog | null>(null);
  // Every machine's profiles, not only this window's: «Новый профиль…» copies a layout from any machine (ОБ.102 ← ОБ.101).
  const [allProfiles, setAllProfiles] = useState<MarkingProfile[]>([]);
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
      setAllProfiles(result.machines.flatMap((item) => item.profiles));
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
        {printer?.hold && <p className="warning-inline" role="alert">Маркировка на паузе: {printer.hold}</p>}
        {machine?.binding && <PrinterClear key={`${machine.equipment_id}:${machine.binding.machine_id}:${machine.binding.printer}`}
          equipmentId={machine.equipment_id} initial={machine.binding.command ?? null} online={Boolean(printer?.online && printer.present)}
          pending={machine.binding.pending_jobs ?? 0} />}
        {canEdit && machine && <div className="marking-binding">
          <label>Принтер на рабочем месте<select name="equipment-printer" autoComplete="off" disabled={locked} value={target} onChange={(event) => setTarget(event.target.value)}>
            <option value="">Выберите принтер…</option>{printers.map((item) => <option key={JSON.stringify([item.machine_id, item.name])} value={JSON.stringify([item.machine_id, item.name])}>{workplaceName(item)} · {item.name}{item.online ? "" : " · Не на связи"}</option>)}</select></label>
          <button type="button" className="secondary" disabled={locked || !target} onClick={() => void bind()}>{busy ? "Сохранение…" : "Привязать принтер"}</button>
        </div>}
        {canEdit && machine && <NewProfile key={machine.equipment_id} machine={machine} sources={sourceProfiles(catalog, allProfiles)} locked={locked}
          onCreated={(created) => { setCatalog((current) => current && { ...current, machines: current.machines.map((item) => item.equipment_id === machine.equipment_id ? { ...item, profiles: [...item.profiles, created] } : item) }); setProfileId(created.id); }} />}
        {profile && <ProfileEditor key={profile.id} profile={profile} canEdit={canEdit} online={Boolean(printer?.online && printer.present)} onBusy={setEditorBusy} onDirty={setEditorDirty}
          onSaved={(updated) => setCatalog((current) => current && { ...current, machines: current.machines.map((item) => ({ ...item, profiles: item.profiles.map((row) => row.id === updated.id ? updated : row) })) })} />}
      </>}
      {!catalog && error && <button type="button" className="secondary" onClick={() => setReload(reload + 1)}>Повторить загрузку</button>}
    </div>
  </PageWindow>;
}

const mm = (value: number) => String(Math.round(value * 100) / 100).replace(".", ",");
const round2 = (value: number) => Math.round(value * 100) / 100;
/** This window's profiles (with unsaved-to-catalog edits applied) first, then every other machine's. */
function sourceProfiles(catalog: MarkingCatalog, all: MarkingProfile[]): MarkingProfile[] {
  const own = catalog.machines.flatMap((item) => item.profiles);
  const ids = new Set(own.map((item) => item.id));
  return [...own, ...all.filter((item) => !ids.has(item.id))];
}

/** The copied layout, with every zone scaled in proportion to the new label size and kept inside it. */
function scaledGeometry(source: MarkingGeometry, width: number, height: number): MarkingGeometry {
  const kx = width / source.width_mm, ky = height / source.height_mm;
  const fit = (zone: MarkingRect): MarkingRect => {
    const w = Math.min(round2(zone.width * kx), width), h = Math.min(round2(zone.height * ky), height);
    // Rounded down to 0.01 mm: x + width must never exceed the label by a float hair (the server refuses that).
    const floor2 = (value: number) => Math.floor(value * 100 + 1e-6) / 100;
    return { x: Math.max(0, Math.min(round2(zone.x * kx), floor2(width - w))), y: Math.max(0, Math.min(round2(zone.y * ky), floor2(height - h))), width: w, height: h };
  };
  return { ...source, width_mm: width, height_mm: height, zones: source.zones.map(fit), blank_zones: source.blank_zones.map(fit) };
}

function NewProfile({ machine, sources, locked, onCreated }: { machine: MarkingMachine; sources: MarkingProfile[]; locked: boolean; onCreated: (profile: MarkingProfile) => void }) {
  const [open, setOpen] = useState(false);
  const [width, setWidth] = useState("");
  const [height, setHeight] = useState("");
  const [name, setName] = useState("");
  const [named, setNamed] = useState(false);
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const w = Number(width.replace(",", ".")), h = Number(height.replace(",", "."));
  const sized = width.trim() !== "" && height.trim() !== "" && w >= 5 && w <= 150 && h >= 5 && h <= 150;
  const distance = (item: MarkingProfile) => Math.abs(item.geometry.width_mm - w) + Math.abs(item.geometry.height_mm - h);
  const pool = machine.profiles.length ? machine.profiles : sources;
  // A profile of the same size on any machine is the best layout (100×20 on ОБ.102 takes ОБ.101's flag zones);
  // otherwise the closest size of this machine. Scaling between different proportions distorts the zones.
  const sameSize = sized ? sources.find((item) => distance(item) < 0.01) : undefined;
  const chosen = sources.find((item) => item.id === source) ?? sameSize ?? (sized ? [...pool].sort((a, b) => distance(a) - distance(b))[0] : pool[0]);
  const shownName = named ? name : sized ? `${mm(w)}×${mm(h)}` : "";
  function start() {
    const first = pool[0];
    setWidth(first ? mm(first.geometry.width_mm) : ""); setHeight(first ? mm(first.geometry.height_mm) : "");
    setName(""); setNamed(false); setSource(""); setError(""); setOpen(true);
  }
  async function create() {
    if (busy || !sized || !chosen || !shownName.trim()) return;
    setBusy(true); setError("");
    try {
      const created = await markingApi.createProfile(machine.equipment_id, { name: shownName, copy_from: chosen.id, geometry: scaledGeometry(chosen.geometry, w, h) });
      setOpen(false); onCreated(created);
    } catch (problem) { setError(failureText(problem, "Профиль не создан")); }
    finally { setBusy(false); }
  }
  if (!open) return <div className="marking-binding"><button type="button" className="secondary" disabled={locked} onClick={start}>Новый профиль…</button></div>;
  return <fieldset className="form-grid marking-new-profile" disabled={busy}>
    <legend>Новый профиль станка {machine.inventory_number}</legend>
    <label>Название<input name="new-profile-name" autoComplete="off" value={shownName} onChange={(event) => { setName(event.target.value); setNamed(true); }} maxLength={80} /></label>
    <label>Ширина, мм<input name="new-profile-width" inputMode="decimal" autoComplete="off" value={width} onChange={(event) => setWidth(event.target.value)} /></label>
    <label>Высота, мм<input name="new-profile-height" inputMode="decimal" autoComplete="off" value={height} onChange={(event) => setHeight(event.target.value)} /></label>
    <label>Копировать разметку из<select name="new-profile-source" autoComplete="off" value={chosen?.id ?? ""} onChange={(event) => setSource(event.target.value)}>
      {sources.map((item) => <option key={item.id} value={item.id}>{item.name} · {mm(item.geometry.width_mm)}×{mm(item.geometry.height_mm)} мм</option>)}</select></label>
    <p className="muted wide">Разметка берётся с профиля того же размера, если он есть у любого станка; иначе зоны масштабируются под новый размер. Профиль создаётся неоткалиброванным — нужна пробная печать.</p>
    {!sized && (width || height) && <p className="warning-inline wide" role="alert">Размер — от 5 до 150 мм по каждой стороне.</p>}
    {error && <p className="warning-inline wide" role="alert">{error}</p>}
    <div className="form-actions wide">
      <button type="button" className="primary" disabled={!sized || !chosen || !shownName.trim()} onClick={() => void create()}>{busy ? "Создание…" : "Создать профиль"}</button>
      <button type="button" className="secondary" onClick={() => setOpen(false)}>Отмена</button></div>
  </fieldset>;
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
  const restoredHalves = splitHalves(restored.record?.text ?? "");
  const flag = profile.geometry.zones.length === 2;
  const [name, setName] = useState(profile.name);
  const [halves, setHalves] = useState<"same" | "split">(flag && restoredHalves ? "split" : "same");
  const [text, setText] = useState(restoredHalves && flag ? restoredHalves[0] : restored.record?.text ?? "АБВ 123");
  const [right, setRight] = useState(restoredHalves && flag ? restoredHalves[1] : "123 АБВ");
  const [preview, setPreview] = useState<MarkingRaster | null>(null);
  const [batchId, setBatchId] = useState(restored.record?.batchId ?? "");
  const [confirmed, setConfirmed] = useState(false);
  const inFlight = useRef(false);
  const canPrint = useCanPrint();
  const geometryDirty = JSON.stringify(geometry) !== JSON.stringify(profile.geometry);
  const raster = (value: MarkingGeometry) => JSON.stringify({ ...value, peel: undefined });
  const rasterDirty = raster(geometry) !== raster(profile.geometry);
  const nameDirty = name.trim().replace(/\s+/g, " ") !== profile.name;
  const dirty = geometryDirty || nameDirty;
  // «Разный» sends «ЛЕВО | ПРАВО»: the same divider an ETK label text uses for the halves of a flag.
  const sample = flag && halves === "split" ? (text.trim() && right.trim() ? joinHalves(text, right) : "") : text;
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  // Reset the form only when the saved profile really changes: on mount the state already equals the profile, and a
  // late mount-time reset used to overwrite the first edit made before passive effects were flushed.
  const syncedProfile = useRef(profile);
  useEffect(() => {
    if (syncedProfile.current === profile) return;
    syncedProfile.current = profile;
    setGeometry(profile.geometry); setName(profile.name); setPreview(null); setConfirmed(false);
  }, [profile]);
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
    // A rename alone keeps the calibration: the server resets it only when the geometry changes.
    const keep = calibrated || (!rasterDirty && profile.calibrated);
    await act(async () => {
      onSaved(await markingApi.profile(profile, geometry, keep, name.trim().replace(/\s+/g, " ")));
      setNotice(calibrated ? "Калибровка подтверждена" : keep ? "Профиль сохранён" : "Профиль сохранён — выполните пробную печать");
    });
  }
  async function calibrate(print: boolean) {
    await act(async () => {
      const request: CalibrationRecord = calibrationIntent?.pending ? calibrationIntent : { text: sample, version: profile.version, key: crypto.randomUUID(), pending: true };
      if (print) {
        try { sessionStorage.setItem(storageName, JSON.stringify(request)); }
        catch { throw new Error("Не удалось сохранить запрос пробной печати — проверьте хранилище браузера"); }
        setCalibrationIntent(request);
      }
      let result;
      try { result = await markingApi.calibration({ ...profile, version: print ? request.version : profile.version }, print ? request.text : sample, print, print ? request.key : undefined); }
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
    <div className="marking-head"><h3>Размеры и расположение текста</h3><span className={profile.calibrated && !rasterDirty ? "tone-ok" : "tone-warn"}>{profile.calibrated && !rasterDirty ? "Откалиброван" : "Не откалиброван"}</span></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {calibrationIntent?.pending && <p className="warning-inline">Проверьте постановку предыдущей пробной этикетки: повторите запрос пробы.</p>}
    {notice && <p className="success-banner" role="status">{notice}</p>}
    <div className="marking-profile-layout"><div className="marking-profile-dimensions">
    <div className="marking-geometry">
      <label className="marking-name">Название профиля<input name="profile-name" autoComplete="off" maxLength={80} disabled={disabled} value={name}
        onChange={(event) => { setName(event.target.value); setNotice(""); }} /></label>
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
      <label className="check-inline marking-peel"><input type="checkbox" name="geometry-peel" disabled={disabled} checked={Boolean(geometry.peel)}
        onChange={(event) => setGeometry((current) => ({ ...current, peel: event.target.checked }))} />Выдача по одной через отделитель</label>
    </div>
    {rectangles("zones", "Текстовые зоны")}{rectangles("blank_zones", "Зоны без печати")}
    {canEdit && <div className="form-actions"><button type="button" className="secondary" disabled={busy || !dirty || !valid || !name.trim()} onClick={() => void save(false)}>Сохранить профиль</button>
      {dirty && <><button type="button" className="secondary" disabled={busy} onClick={() => { setGeometry(profile.geometry); setName(profile.name); setError(""); }}>Отменить изменения</button>
        <span className="muted">Сохраните или отмените изменения перед закрытием.</span></>}</div>}
    </div><div className="marking-calibration">
      <h3>Пробная печать</h3>
      {flag && <label>Текст на половинах<select name="marking-calibration-halves" autoComplete="off" disabled={disabled} value={halves}
        onChange={(event) => { setHalves(event.target.value as "same" | "split"); setPreview(null); setConfirmed(false); }}>
        <option value="same">Одинаковый на обеих половинах</option><option value="split">Разный на левой и правой</option></select></label>}
      <label>{flag && halves === "split" ? "Левая половина" : "Текст пробной этикетки"}<textarea name="marking-calibration-text" autoComplete="off" maxLength={flag && halves === "split" ? 480 : 1000} disabled={disabled} value={text}
        onChange={(event) => { setText(event.target.value); setPreview(null); setConfirmed(false); }} /></label>
      {flag && halves === "split" && <label>Правая половина<textarea name="marking-calibration-right" autoComplete="off" maxLength={480} disabled={disabled} value={right}
        onChange={(event) => { setRight(event.target.value); setPreview(null); setConfirmed(false); }} /></label>}
      {geometryDirty && <p className="warning-inline">Сохраните размеры перед пробной печатью.</p>}
      <div className="form-actions"><button type="button" className="secondary" disabled={disabled || geometryDirty || !sample.trim()} onClick={() => void calibrate(false)}>Показать пробную этикетку</button>
        {canPrint && <button type="button" className="primary" disabled={busy || !canEdit || Boolean(restored.error) || geometryDirty || !sample.trim() || (!online && !calibrationIntent?.pending)} onClick={() => void calibrate(true)}>{busy ? "Обработка…" : calibrationIntent?.pending ? "Повторить запрос пробы" : "Пробная печать"}</button>}</div>
      {preview && <figure className="marking-raster"><figcaption>Пробная этикетка · <span className="tnum">{formatNumber(preview.width_mm)} × {formatNumber(preview.height_mm)} мм</span></figcaption>
        <img src={`data:image/png;base64,${preview.png_b64}`} alt={`Пробная этикетка: ${sample}`}
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

/** «Очистить принтер»: the agent removes ERP marking jobs from the Windows queue and empties the Godex buffer. */
function PrinterClear({ equipmentId, initial, online, pending }: { equipmentId: string; initial: MarkingPrinterCommand | null; online: boolean; pending: number }) {
  const canAct = useCanAct("production");
  const [command, setCommand] = useState(initial);
  const [reason, setReason] = useState<string | null>(null);
  const [cancelPending, setCancelPending] = useState(false);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const open = command?.status === "pending" || command?.status === "taken";
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      markingApi.printerCommand(equipmentId, controller.signal).then((result) => { if (!controller.signal.aborted) setCommand(result); })
        .catch((problem) => { if (!controller.signal.aborted) setError(failureText(problem, "Не удалось получить итог очистки")); });
    }, 2000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [equipmentId, open, command]);
  async function clear() {
    if (busy || !reason?.trim()) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await markingApi.clearPrinter(equipmentId, reason.trim(), cancelPending && pending > 0);
      setCommand(result); setReason(null); setCancelPending(false);
      if (result.cancelled_jobs) setNotice(`Отменено этикеток партий: ${formatNumber(result.cancelled_jobs)}`);
    } catch (problem) { setError(failureText(problem, "Очистка не запрошена")); }
    finally { setBusy(false); }
  }
  const tone = command?.status === "done" ? "tone-ok" : command?.status === "failed" ? "tone-danger" : "tone-info";
  return <div className="marking-clear">
    {command && <p className="marking-clear-state"><span className={tone}>Очистка принтера: {command.status_label}</span>
      <span className="muted tnum">{formatDateTime(command.finished_at ?? command.created_at)} · {command.requested_by}</span>
      {command.message && <span>{command.message}</span>}</p>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <p className="success-banner" role="status">{notice}</p>}
    {canAct && reason === null && <div className="form-actions">
      <button type="button" className="secondary" disabled={busy || open || !online} onClick={() => setReason("")}>Очистить принтер…</button>
      {!online && <span className="muted">Принтер не на связи</span>}</div>}
    {canAct && reason !== null && <div className="marking-resolution">
      <p className="marking-action-note">Агент удалит задания ERP из очереди Windows и очистит буфер Godex (~S,BUFCLR) — этикетки, уже переданные принтеру, не выйдут.
        {pending > 0 ? ` Этикетки партий, ещё не переданные принтеру (${formatNumber(pending)} шт.), после очистки продолжат печататься, если их не отменить.` : ""}</p>
      {pending > 0 && <label className="check-inline marking-action-note"><input type="checkbox" name="marking-clear-cancel" checked={cancelPending} disabled={busy}
        onChange={(event) => setCancelPending(event.target.checked)} />Отменить оставшиеся {formatNumber(pending)} эт. партий на этом принтере</label>}
      <label>Причина очистки<input name="marking-clear-reason" autoComplete="off" maxLength={1000} disabled={busy} value={reason}
        onChange={(event) => setReason(event.target.value)} /></label>
      <div className="form-actions"><button type="button" className="primary" disabled={busy || !reason.trim()} onClick={() => void clear()}>{busy ? "Обработка…" : "Очистить принтер"}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => setReason(null)}>Назад</button></div>
    </div>}
  </div>;
}
