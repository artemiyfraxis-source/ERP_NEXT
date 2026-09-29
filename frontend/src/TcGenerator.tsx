import { useEffect, useMemo, useState } from "react";
import {
  referencesApi,
  techcardsApi,
  type GrapherImportResult,
  type KitsetBom,
  type ProjectTechcard,
  type TerminalResolution,
} from "./api";
import {
  FOLD_STRIP_MM_,
  JACKET_STRIP_MM_,
  WRAP_SLOTS_,
  addEndpoint,
  applyGrapherGroups,
  coaxDiaFromRecords,
  coaxFromFields,
  coaxRecordFields,
  emptyState,
  initState,
  makeGen,
  onCoaxConnChange,
  opFromFields,
  parseDecimal_,
  sideRu_,
  switchGenTab,
  syncCoverRow_,
  syncHsWithBraid_,
  terFromFields,
  toggleLengthMode,
  toggleRoute,
  twistPitchForZ0_,
  twistZ0_,
  updateEndpoint,
  updateWire,
  wireDiaFromRecords,
  type Comp,
  type Endpoint,
  type GenData,
  type GenState,
  type Side,
  type Tab,
  type Tmpl,
  type WrapKind,
} from "./tcGenLogic";

type Refs = Pick<GenData, "templates" | "ops" | "terRecords" | "coaxRecords" | "wireDia" | "coaxCableDia">;

const EMPTY_REFS: Refs = { templates: [], ops: [], terRecords: [], coaxRecords: [], wireDia: null, coaxCableDia: null };
const TABS: Array<[Tab, string]> = [["inter", "Межплатная"], ["complex", "Сложная сборка"], ["coax", "КОАКС"]];
const WRAP_KINDS: Array<[WrapKind, string]> = [
  ["tape", "Лента — последней операцией"], ["braid", "Оплётка — до термоусадки"], ["under", "Лента под оплётку — перед ней"],
];

function badgeCls(t: string) {
  const lc = (t || "").toLowerCase();
  if (lc.includes("провод") || lc.includes("wire") || lc.includes("кабель")) return "badge-wire";
  if (lc.includes("терминал") || lc.includes("terminal") || lc.includes("контакт")) return "badge-term";
  if (lc.includes("соед") || lc.includes("conn") || lc.includes("разъ") || lc.includes("корпус")) return "badge-conn";
  return "badge-other";
}

async function loadRefs(): Promise<Refs> {
  const safe = <T,>(promise: Promise<T>, fallback: T) => promise.catch(() => fallback);
  const noRecords = { metadata: null, columns: [], records: [] };
  const [templates, op, ter, coax, kabAwg, kabCoax] = await Promise.all([
    safe(techcardsApi.templates(), { metadata: null, items: [] }),
    safe(referencesApi.techOperations("op", "", 5000), noRecords),
    safe(referencesApi.techOperations("ter", "", 5000), noRecords),
    safe(referencesApi.techOperations("coax", "", 5000), noRecords),
    safe(referencesApi.techOperations("kab_awg", "", 5000), noRecords),
    safe(referencesApi.techOperations("kab_coax", "", 5000), noRecords),
  ]);
  return {
    templates: (templates?.items || []).map((item): Tmpl => ({ id: item.id, title: item.title, category: item.category })),
    ops: (op?.records || []).map((record) => opFromFields(record.fields)),
    terRecords: (ter?.records || []).map((record) => terFromFields(record.id, record.fields)).filter((record) => record.article),
    coaxRecords: (coax?.records || []).map((record) => coaxFromFields(record.fields)),
    wireDia: wireDiaFromRecords((kabAwg?.records || []).map((record) => record.fields)),
    coaxCableDia: coaxDiaFromRecords((kabCoax?.records || []).map((record) => record.fields)),
  };
}

/**
 * Генератор техкарт — порт формы SUPPLYMAP TcGenInline.html. Собирает тот же config, что уходил в
 * portalTechmapGenerate, и отдаёт его в `onBuild` (→ POST /api/techcards/projects/{id}/build).
 * Данные — `portalTcGenData` → `tcLoadBomFromKit`: состав сборки из набора проекта и строки листа КБn расчёта
 * (`kit`, GET /api/techcards/kitsets/{project}/bom/{index}); нет набора — ошибка вместо формы (`kitError`).
 */
export function TcGenerator({ card, kit, kitError, busy, onBuild, onDiscard, onConfig }: {
  card: ProjectTechcard;
  kit?: KitsetBom | null;
  kitError?: string;
  busy: boolean;
  onBuild: (config: Record<string, unknown>) => Promise<void> | void;
  onDiscard: () => Promise<void> | void;
  onConfig?: (config: Record<string, unknown>) => void;
}) {
  const [refs, setRefs] = useState<Refs | null>(null);
  const [S, setS] = useState<GenState>(emptyState);
  const [status, setStatus] = useState<{ text: string; error: boolean }>({ text: "", error: false });
  const [grapher, setGrapher] = useState<{ data: GrapherImportResult; pick: number } | null>(null);
  const [terEdits, setTerEdits] = useState<Record<string, Record<string, string>>>({});
  const [terNorms, setTerNorms] = useState<Record<string, TerminalResolution | null>>({});
  const [coaxAdd, setCoaxAdd] = useState<Record<string, Record<string, string>>>({});

  useEffect(() => { void loadRefs().then(setRefs); }, []);

  const components = useMemo<Comp[]>(() => (kit?.components || []).map((item) => ({
    id: item.id, type: item.type || "", name: item.name || "", art: item.art || "", qty: item.qty,
  })), [kit]);
  // tcLoadBomFromKit: «Кол-во» — тираж сборки, партию генератор берёт отсюда.
  const assemblyInfo = useMemo<Record<string, string>>(() => ({
    "Индекс": kit?.index || "", "Наименование": kit?.name || "", "Кол-во": kit?.run || "",
  }), [kit]);
  const D: GenData = useMemo(() => ({
    ...(refs || EMPTY_REFS), components, assemblyInfo, partQty: Number(kit?.part_qty) || 1, calcOps: kit?.calc_ops || [],
  }), [refs, components, assemblyInfo, kit]);
  const gen = useMemo(() => makeGen(D, S), [D, S]);

  // init(): каждый монтаж засевает форму из состава набора и строк расчёта (autoFillAllWires, applyCalcTin_).
  useEffect(() => {
    if (!refs || !kit) return;
    setS(initState(D));
    setStatus({ text: "", error: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refs, card.id, kit]);

  // renderOpsCard: шаблон, показанный первым, запоминается (selectedTemplates) и дальше не пересчитывается.
  useEffect(() => {
    const next = gen.stickTemplates();
    if (next) setS((current) => ({ ...current, selectedTemplates: next }));
  }, [gen]);

  const ops = useMemo(() => gen.getActiveOps(), [gen]);
  const config = useMemo(() => gen.buildConfig(), [gen]);
  useEffect(() => { onConfig?.(config); }, [config]); // eslint-disable-line react-hooks/exhaustive-deps

  // Нормы терминала по сечению проводов разъёма (TcGenerator.gs:762-788) — через справочник ERP.
  const crimpEndpoints = S.tab === "coax" ? [] : S.endpoints.filter((ep) => ep.method === "crimp" && ep.termId);
  const normKey = JSON.stringify(crimpEndpoints.map((ep) => {
    const term = gen.getCompById(ep.termId);
    const routed = new Set(gen.routedWireIdxs_(ep));
    const wires = gen.activeWires_().filter((w) => routed.has(w.idx)).map((w) => { const c = gen.getCompById(w.compId); return c ? c.art || c.name : ""; }).filter(Boolean);
    return [ep.id, term ? term.art || term.name : "", wires, !!ep.fold && S.tab === "complex"];
  }));
  const gostAwg = D.wireDia?.gostAwg;
  useEffect(() => {
    const items = JSON.parse(normKey) as Array<[string, string, string[], boolean]>;
    let alive = true;
    void Promise.all(items.map(async ([id, article, wires, folded]) => {
      if (!article || !wires.length) return [id, null] as const;
      const result = await referencesApi.resolveTerminal({ article, wires, folded, ...(gostAwg ? { gost_awg: gostAwg } : {}) }).catch(() => null);
      return [id, result] as const;
    })).then((pairs) => { if (alive) setTerNorms(Object.fromEntries(pairs)); });
    return () => { alive = false; };
  }, [normKey, gostAwg]);

  // Любая правка формы перерисовывает карточку операций (refresh → renderOpsCard): ручные времена сбрасываются
  // к БД.ОП. Правка самих времён — setTime, она карточку не перерисовывает.
  const update = (patch: Partial<GenState> | ((current: GenState) => GenState)) =>
    setS((current) => ({ ...(typeof patch === "function" ? patch(current) : { ...current, ...patch }), times: {} }));
  const complex = S.tab === "complex";
  const { terms: bomTerms, conns: bomConns } = gen.bomLists();
  const wireOptions = gen.wireComps_();
  const active = gen.activeWires_();
  const compLabel = (id: string) => { const c = gen.getCompById(id); return c ? c.art || c.name : "?"; };
  const cut = gen.cutLengthInfo();
  const hasB = gen.endpointsOnSide_("B").length > 0;
  const crimpSides = gen.crimpCutSides_();
  const note = gen.twistNote();

  function selectComps(list: Comp[], value: string, onChange: (id: string) => void, label: string, empty = "— нет —") {
    return <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{empty}</option>
      {list.map((c) => <option key={c.id} value={c.id}>{c.art || c.name}</option>)}
    </select>;
  }

  async function grapherFile(file: File) {
    setStatus({ text: `Читаю чертёж «${file.name}»…`, error: false });
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
      const data = await techcardsApi.importGrapher(file.name, dataUrl.split(",", 2)[1] || "",
        wireOptions.map((c) => ({ id: c.id, article: c.art || "", name: c.name || "" })));
      if (!data.harnesses.length) { setStatus({ text: "В проекте чертежа нет жгутов.", error: true }); return; }
      const index = data.harnesses.findIndex((h) => h.designation.trim().toLowerCase() === card.item_index.trim().toLowerCase());
      setGrapher({ data, pick: index >= 0 ? index : 0 });
      setStatus({ text: `Чертёж прочитан · жгутов: ${data.harnesses.length}`, error: false });
    } catch (reason) {
      setStatus({ text: reason instanceof Error ? reason.message : "Экспорт TECHMAP-GRAPHER не разобран", error: true });
    }
  }

  function grapherApply() {
    if (!grapher) return;
    const harness = grapher.data.harnesses[grapher.pick];
    // Не найденный в составе материал остаётся пустым кабелем — «не найдено — выбери».
    const groups = harness.groups.map((group) => ({
      compId: group.component_id && components.some((c) => c.id === group.component_id) ? group.component_id : "",
      qty: group.quantity, length: Number(group.length_mm) || 0, cores: group.cores,
      jacket: group.jacket_mm == null ? "" : String(group.jacket_mm),
    }));
    update((current) => applyGrapherGroups(current, groups));
    setStatus({ text: `Кабели из ${harness.designation || "чертежа"} подставлены в «Кабели»`, error: false });
  }

  async function saveTer(ep: Endpoint) {
    const term = gen.getCompById(ep.termId);
    if (!term) return;
    const rec = gen.findTerRecord_(term.art);
    const edits = terEdits[ep.id] || {};
    const keys: Array<[string, string]> = [["lPlus", "terLPlus"], ["lMinus", "terLMinus"], ["step", "terStep"], ["applicator", "terApplicator"]];
    try {
      if (rec?.id) {
        const changes = keys.filter(([key]) => key in edits && edits[key] !== (rec as unknown as Record<string, string>)[key])
          .map(([key, field]) => ({ field, old: rec.fields?.[field] || "", value: edits[key].trim() }));
        if (changes.length) await referencesApi.editRecord(rec.id, changes);
      } else {
        if (!window.confirm(`Артикул «${term.art}» не найден в БД.ТЕР.\nДобавить новую запись?`)) return;
        await referencesApi.createRecord("ter", { terArticle: term.art, ...Object.fromEntries(keys.map(([key, field]) => [field, (edits[key] || "").trim()])) });
      }
      setTerEdits((current) => { const next = { ...current }; delete next[ep.id]; return next; });
      setRefs(await loadRefs());
      setStatus({ text: rec?.id ? "✓ Готово" : "✓ Добавлено в БД", error: false });
    } catch (reason) {
      setStatus({ text: "⚠ " + (reason instanceof Error ? reason.message : "Ошибка"), error: true });
    }
  }

  async function saveCoaxToDb(side: Side) {
    const s = side === "A" ? S.coax.sideA : S.coax.sideB;
    if (!s || !s.art) return;
    const v = (key: string) => (coaxAdd[side]?.[key] || "").trim();
    const payload = {
      connArticle: s.art, type: v("type"), wire: v("wire") || gen.coaxBomCable_(), mfr: "", program: "",
      // D1–D3 не пишем: диаметры у кабеля (СПР.КАБ), генератор берёт их оттуда.
      d1: "", d2: "", d3: "",
      l1: v("l1"), l2: v("l2"), l3: v("l3"), lPlus: v("lPlus"), lMinus: v("lMinus"),
      pinType: side === "A" ? S.coax.pinA : S.coax.pinB, shieldType: S.coax.shield,
    };
    try {
      await referencesApi.createRecord("coax", coaxRecordFields(payload));
      const fresh = await loadRefs();
      setRefs(fresh);
      setS((current) => onCoaxConnChange({ ...D, ...fresh }, current, side));
      setStatus({ text: `«${s.art}» добавлен в БД.КОАКС`, error: false });
    } catch (reason) {
      window.alert("Ошибка сохранения: " + (reason instanceof Error ? reason.message : String(reason)));
    }
  }

  async function generate() {
    const { cfg, error, warnings } = gen.validate();
    if (error) { setStatus({ text: error, error: true }); return; }
    if (warnings.length && !window.confirm("⚠ Предупреждения:\n\n" + warnings.join("\n") + "\n\nВсё равно создать техкарты?")) {
      setStatus({ text: "Генерация отменена — проверьте предупреждения.", error: true });
      return;
    }
    if (card.status === "Выдана" && !window.confirm(`Техкарта ${card.item_index} уже выдана.\n\nПересобрать? ЭТК пересоберётся новой ревизией.`)) {
      setStatus({ text: "Пересборка отменена.", error: true });
      return;
    }
    setStatus({ text: card.status === "Выдана" ? "Пересобираю техкарты…" : "Создаю техкарты…", error: false });
    await onBuild(cfg);
    setStatus({ text: "", error: false });
  }

  async function deleteGenerated() {
    if (!card.document) { setStatus({ text: "Техкарта ещё не собиралась.", error: true }); return; }
    if (card.status === "Выдана") { setStatus({ text: "Выданную ЭТК удалить нельзя — пересоберите её новой ревизией.", error: true }); return; }
    if (!window.confirm("Удалить собранный черновик ЭТК?")) return;
    await onDiscard();
  }

  function twistPitchInput(value: string) {
    const g = gen.twistGeom_();
    const p = parseDecimal_(value);
    const z = g && p > 0 ? twistZ0_(g.d, g.D, p, g.eps) : 0;
    update((current) => ({ ...current, twist: { ...current.twist, pitch: value, z0: g && p > 0 ? (z > 0 ? String(Math.round(z)) : "") : current.twist.z0 } }));
  }
  function twistZ0Input(value: string) {
    const g = gen.twistGeom_();
    const z0 = parseDecimal_(value);
    const res = g && z0 > 0 ? twistPitchForZ0_(g.d, g.D, z0, g.eps) : null;
    update((current) => ({ ...current, twist: { ...current.twist, z0: value, pitch: res ? String(res.p).replace(".", ",") : current.twist.pitch } }));
  }

  function endpointCard(ep: Endpoint) {
    const ord = gen.endpointOrdinal_(ep);
    const crimp = ep.method === "crimp";
    const routedList = gen.routedWireIdxs_(ep);
    const routed = new Set(routedList);
    const term = gen.getCompById(ep.termId);
    const ter = term ? gen.findTerRecord_(term.art) : null;
    const set = (patch: Partial<Endpoint>) => update((current) => updateEndpoint(D, current, ep.id, patch));
    const single = gen.endpointsOnSide_(ep.side).length < 2;
    const showRouting = complex && active.length > 0 && !(single && !S.endpoints.some((e) => e.side !== ep.side));
    const chips = active.map((w, pos) => <label className="ep-chip" key={w.idx}>
      <input type="checkbox" checked={routed.has(w.idx)} onChange={() => update((current) => toggleRoute(current, makeGen(D, current), ep.id, w.idx))} />
      {` Кабель ${pos + 1} (${compLabel(w.compId)})`}
    </label>);
    const terValues = { lPlus: ter?.lPlus || "", lMinus: ter?.lMinus || "", step: ter?.step || "", applicator: ter?.applicator || "", ...(terEdits[ep.id] || {}) };
    const terDirty = !!terEdits[ep.id] && Object.entries(terEdits[ep.id]).some(([key, value]) => value !== ((ter as unknown as Record<string, string>)?.[key] || ""));
    const norm = terNorms[ep.id];
    return <div className="ep-card" key={ep.id}>
      <div className="ep-head"><span className="ep-badge">{sideRu_(ep.side) + ord}</span>
        <button className="btn-remove" title="Удалить разъём" onClick={() => update((current) => ({ ...current, endpoints: current.endpoints.filter((e) => e.id !== ep.id) }))}>×</button></div>
      <label className="field-label">Разъём / Корпус{selectComps(bomConns, ep.connId, (id) => set({ connId: id }), `Разъём ${sideRu_(ep.side)}${ord}`)}</label>
      {complex && <label className="field-label">Наименование по чертежу<input value={ep.label} placeholder="напр. GIMBAL, NODE, PWR…" onChange={(event) => set({ label: event.target.value })} /></label>}
      <div className="ep-method">
        <label><input type="radio" name={`m_${ep.id}`} checked={crimp} onChange={() => set({ method: "crimp" })} /> Обжим</label>
        <label><input type="radio" name={`m_${ep.id}`} checked={!crimp} onChange={() => set({ method: "solder" })} /> Пайка</label>
        {ep.methodAuto && <span className="ep-auto">авто</span>}
      </div>
      {crimp ? <>
        <label className="field-label">Терминал{selectComps(bomTerms, ep.termId, (id) => set({ termId: id }), `Терминал ${sideRu_(ep.side)}${ord}`)}</label>
        {term && <small className={ter ? "ok-text" : "danger-text"}>{routedList.length} шт{ter ? "" : " · нет в БД.ТЕР"}</small>}
        {term && <div className="ter-row">
          {([["lPlus", "L+, мм"], ["lMinus", "L−, мм"], ["step", "Шаг"], ["applicator", "Аппликатор"]] as const).map(([key, label]) =>
            <label key={key} className="field-label">{label}<input className={terEdits[ep.id]?.[key] !== undefined ? "f-warn" : terValues[key] ? "f-ok" : "f-empty"} value={terValues[key]} placeholder="—"
              onChange={(event) => setTerEdits((current) => ({ ...current, [ep.id]: { ...(current[ep.id] || {}), [key]: event.target.value } }))} /></label>)}
          {terDirty && <button className="secondary small" onClick={() => void saveTer(ep)}>Записать в БД</button>}
        </div>}
        {norm && <small className="muted">Подбор по сечению: {norm.by_section.map((item) => `${item.section || "сечение?"} → ${item.found ? `${item.applicator || "аппл. —"}, h=${item.crimp_height || "—"}${item.pull_force ? `, F=${item.pull_force}` : ""}${item.range_only ? " (по вилке)" : ""}` : "нет нормы"}`).join("; ")}</small>}
        <label className="ep-otp"><input type="checkbox" checked={ep.otp} onChange={(event) => set({ otp: event.target.checked })} /> OTP — обжим без зачистки (провод даётся зачищенным)</label>
        {complex ? <>
          <label className="ep-otp"><input type="checkbox" checked={ep.double} onChange={(event) => set({ double: event.target.checked })} /> Групповой обжим — по
            <input type="number" min={2} max={6} value={gen.epPer_(ep)} style={{ width: 42 }} onChange={(event) => set({ perTerm: Math.max(2, parseInt(event.target.value, 10) || 2) })} /> провода в 1 терминал (норма терминалов = группы)</label>
          <label className="ep-otp"><input type="checkbox" checked={!!ep.fold} onChange={(event) => set({ fold: event.target.checked })} /> Складывание жилы — увеличение сечения под контакт (зачистка {FOLD_STRIP_MM_} мм, сложить вдвое)</label>
        </> : <label className="ep-otp"><input type="checkbox" checked={ep.double} onChange={(event) => set({ double: event.target.checked })} /> Двойной обжим — 2 провода в 1 терминал (норма терминалов = пары)</label>}
      </> : <>
        <div className={ep.sdrTmpl ? "ep-sdr" : "ep-sdr ep-sdr-warn"}>{ep.sdrTmpl ? <>Паяльный разъём → шаблон <strong>{ep.sdrTmpl}</strong></> : "Тип не распознан — пайка без авто-шаблона (выберите вручную на стадии операций)"}</div>
        <div className="ter-row">
          <label className="field-label">Зачистка под пайку, мм<input value={ep.solderStrip} placeholder="напр. 5" onChange={(event) => set({ solderStrip: event.target.value })} /></label>
          {!ep.tutOff && <label className="field-label">Длина ТУТ, мм<input value={ep.tutLen} placeholder="напр. 20" onChange={(event) => set({ tutLen: event.target.value })} /></label>}
        </div>
        <label className="ep-otp"><input type="checkbox" checked={!ep.tutOff} onChange={(event) => set({ tutOff: !event.target.checked })} /> ТУТ на каждый провод</label>
      </>}
      {showRouting && (single
        ? <details className="ep-pinout" open={!active.every((w) => routed.has(w.idx))}><summary>Провода в этот разъём — {active.every((w) => routed.has(w.idx)) ? "все" : "не все: остальные — свободные концы"}</summary><div className="ep-route">{chips}</div></details>
        : <div className="field-label">Провода в этот разъём<div className="ep-route">{chips}</div></div>)}
      {complex && active.length > 0 && <details className="ep-pinout" open={active.some((w) => routed.has(w.idx) && ep.pins?.[w.idx])}>
        <summary>Распиновка — контакты разъёма{active.some((w) => routed.has(w.idx) && ep.pins?.[w.idx]) ? " ✓" : ""}</summary>
        {active.filter((w) => routed.has(w.idx)).map((w) => <div className="ep-pin" key={w.idx}>
          <span>{compLabel(w.compId)}{w.qty > 1 ? ` ×${w.qty}` : ""}</span>
          <input value={ep.pins?.[w.idx] || ""} placeholder={w.qty > 1 ? `1, 3 или 1-${w.qty}` : "конт."} onChange={(event) => {
            const value = event.target.value.trim();
            const pins = { ...(ep.pins || {}) };
            if (value) pins[w.idx] = value; else delete pins[w.idx];
            set({ pins });
          }} />
        </div>)}
      </details>}
    </div>;
  }

  function coaxSideBlock(side: Side) {
    const s = side === "A" ? S.coax.sideA : S.coax.sideB;
    const info = gen.coaxInfo(side);
    const pinKey = side === "A" ? "pinA" : "pinB";
    const coverKey = side === "A" ? "coverA" : "coverB";
    const add = coaxAdd[side] || {};
    const addValue = (key: string, fallback = "") => add[key] ?? fallback;
    const setAdd = (key: string, value: string) => setCoaxAdd((current) => ({ ...current, [side]: { ...(current[side] || {}), [key]: value } }));
    return <div className="gcard">
      <div className="field-label">Разъём (из BOM)<strong>{s ? s.art : side === "A" ? "— в BOM нет разъёма —" : "—"}</strong></div>
      <label className="field-label">Тип пина<select aria-label={`Тип пина ${side}`} value={S.coax[pinKey]} onChange={(event) => update((current) => syncCoverRow_({ ...current, coax: { ...current.coax, [pinKey]: event.target.value } }, side))}>
        <option value="пайка">пайка</option><option value="внутри корпуса">пайка внутри корпуса</option>
      </select></label>
      {S.coax[pinKey] === "внутри корпуса" && <label className="field-label">Крышка (угловой разъём)<select value={S.coax[coverKey]} onChange={(event) => update((current) => ({ ...current, coax: { ...current.coax, [coverKey]: event.target.value } }))}>
        <option value="">без крышки</option><option value="обжим">крышка обжим</option><option value="винт">крышка винт</option>
      </select></label>}
      <small className={info.missing ? "danger-text" : "muted"}>{info.text}</small>
      {info.missing && <div className="coax-add">
        <small>Нет в БД.КОАКС — введите размеры и добавьте:</small>
        <label className="field-label">Тип/Серия<input value={addValue("type")} placeholder="напр. SMA-male R/A" onChange={(event) => setAdd("type", event.target.value)} /></label>
        <label className="field-label">Провод<input value={addValue("wire", gen.coaxBomCable_())} onChange={(event) => setAdd("wire", event.target.value)} /></label>
        {/* D1–D3 — у кабеля (СПР.КАБ), у разъёма только длины разделки. */}
        <div className="coax-dims">{(["l1", "l2", "l3", "lPlus", "lMinus"] as const).map((key) =>
          <input key={key} aria-label={`${key} ${side}`} placeholder={{ l1: "L1", l2: "L2", l3: "L3", lPlus: "L+", lMinus: "L-" }[key]}
            value={addValue(key)} onChange={(event) => setAdd(key, event.target.value)} />)}
        </div>
        <button className="secondary small" disabled={busy} onClick={() => void saveCoaxToDb(side)}>Добавить в БД.КОАКС</button>
      </div>}
    </div>;
  }

  if (kitError) return <div className="tcgen"><div className="error-banner" role="alert">{kitError}</div></div>;
  if (!refs || !kit) return <div className="tcgen"><div className="empty">Читаю состав {card.item_index}…</div></div>;

  const wrapOpts = gen.wrapCandidates().list;
  const capTubes = gen.capTubes();
  const hsComps = S.hs.rows.map((row) => ({ row, comp: gen.getCompById(row.compId) }));
  const marks = gen.markComps_();

  return <div className="tcgen">
    <div className="gen-tabs" role="tablist">{TABS.map(([tab, label]) =>
      <button key={tab} type="button" role="tab" aria-selected={S.tab === tab} className={S.tab === tab ? "gen-tab-btn gactive" : "gen-tab-btn"}
        onClick={() => update((current) => switchGenTab(current, tab))}>{label}</button>)}</div>

    <h3>Данные из листа</h3>
    <div className="gcard">
      <div className="info-kv">{Object.entries(assemblyInfo).filter(([, value]) => value !== "").map(([key, value]) => <span className="info-pair" key={key}><span>{key}: </span><strong>{value}</strong></span>)}</div>
      <table className="spy"><thead><tr><th>Тип</th><th>Название</th><th>Артикул</th><th>Кол-во</th></tr></thead><tbody>
        {components.map((c) => <tr key={c.id}><td><span className={`gbadge ${badgeCls(c.type)}`}>{c.type}</span></td><td>{c.name}</td><td className="muted">{c.art}</td><td>{String(c.qty)}</td></tr>)}
        {!components.length && <tr><td colSpan={4} className="empty">СПЯ не найдена</td></tr>}
      </tbody></table>
    </div>

    {S.tab !== "coax" && <div className={complex ? "tab-inter" : "tab-inter gen-basic"}>
      {gen.complexWarning() && <div className="warning-inline">{gen.complexWarning()}</div>}
      <div className="section-hdr"><h3>Кабели</h3>
        {complex && <button type="button" className="btn-link" onClick={() => update((current) => toggleLengthMode(current, makeGen(D, current).sharedLength()))}>{S.advancedLength ? "← общая длина" : "раздельная длина ▸"}</button>}
        <label className="btn-link">из чертежа<input aria-label="Экспорт TECHMAP-GRAPHER" type="file" hidden accept=".json,.zip,.techmap-project.zip" disabled={busy} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void grapherFile(file); }} /></label>
      </div>
      {grapher && (() => {
        const h = grapher.data.harnesses[grapher.pick];
        const our = gen.getLengthAdjustment_();
        const sg = (x: string | number) => (Number(x) >= 0 ? "+" : "") + x;
        return <div className="gcard grapher-box">
          <div className="grapher-head"><strong>Чертёж {grapher.data.project.designation || grapher.data.project.name}</strong>
            {grapher.data.harnesses.length > 1 ? <select value={grapher.pick} onChange={(event) => setGrapher({ ...grapher, pick: Number(event.target.value) })}>
              {grapher.data.harnesses.map((x, i) => <option key={i} value={i}>{x.designation} × {x.quantity}</option>)}</select>
              : <span>жгут {h.designation} × {h.quantity}</span>}
            <button className="primary small" onClick={grapherApply}>Подставить в «Кабели»</button>
            <button className="btn-link" onClick={() => setGrapher(null)}>скрыть</button></div>
          <table><thead><tr><th>Материал в чертеже</th><th>Позиция набора</th><th>Шт</th><th>По чертежу, мм</th><th>Поправки</th><th>Рез в чертеже</th><th>Концы</th></tr></thead><tbody>
            {h.groups.map((g, i) => <tr key={i}><td>{g.material || "—"}{g.cores > 1 ? ` · ${g.cores} жил${g.jacket_mm != null ? ", разделка " + g.jacket_mm : ""}` : ""}</td>
              <td>{g.component_id && gen.getCompById(g.component_id) ? compLabel(g.component_id) : <span className="danger-text">не найдено — выбери</span>}</td>
              <td>{g.quantity}</td><td>{g.length_mm == null ? <span className="danger-text">нет</span> : g.length_mm}</td>
              <td>{Number(g.correction_from_mm) || Number(g.correction_to_mm) ? `${sg(g.correction_from_mm)} / ${sg(g.correction_to_mm)}` : "—"}{g.mixed_corrections ? " (разные)" : ""}</td>
              <td>{g.cut_mm == null ? "—" : g.cut_mm}</td><td className="muted">{g.ends.join("; ")}{g.quantity > g.ends.length ? " …" : ""}</td></tr>)}
          </tbody></table>
          <small className="muted">Припуск у нас: {our.parts.length ? `${our.parts.join(", ")} = ${our.total} мм` : "концы ещё не выбраны"} — длина реза считается по нему. Разъёмы: {h.connectors.map((c) => `${c.designation} ${c.part_number}${c.terminals.length ? ` (терм. ${c.terminals.join(", ")})` : ""}`).join("; ") || "нет"}{h.pairs ? `. Свитых пар в чертеже: ${h.pairs} — включи свивку ниже.` : "."}</small>
          {h.warnings.map((warning) => <div className="warning-inline" key={warning}>{warning}</div>)}
        </div>;
      })()}
      <div className="gcard">
        <div className="radio-row">
          {([["inner", "Между разъёмами (+L+)"], ["outer", "От краёв разъёмов (−L−)"], ["cut", "Длина реза по КД (без припуска)"]] as const).map(([value, label]) =>
            <label key={value}><input type="radio" name={`measureMode-${card.id}`} checked={S.measureMode === value} onChange={() => update({ measureMode: value })} /> {label}</label>)}
        </div>
        {!S.advancedLength && <label className="field-label">Длина по чертежу, мм<input aria-label="Длина по чертежу" type="number" min={1} placeholder="—" value={S.sharedLengthText}
          onChange={(event) => update({ sharedLengthText: event.target.value, drawingLength: parseDecimal_(event.target.value) || 0 })} /></label>}
        {!S.advancedLength && cut.tone !== "none" && <div className={cut.tone === "warn" ? "cut-info warn" : "cut-info"}><small>{cut.formula}</small><div>Длина реза: <strong>{cut.value}</strong></div></div>}
        {!S.wires.length && <div className="empty">Нет кабелей</div>}
        {S.wires.map((w, n) => {
          const cores = gen.wireCores_(w);
          return <div className="wire-entry" key={w.idx}>
            <label className="field-label">Кабель {n + 1}{selectComps(wireOptions, w.compId, (id) => update((current) => updateWire(current, w.idx, { compId: id })), `Кабель ${n + 1}`, "— выберите —")}</label>
            {S.advancedLength && <label className="field-label">Длина по чертежу, мм<input type="number" min={1} value={w.length || ""} onChange={(event) => update((current) => updateWire(current, w.idx, { length: parseDecimal_(event.target.value) || 0 }))} /></label>}
            <label className="field-label">Кол-во<input aria-label={`Кол-во кабеля ${n + 1}`} type="number" min={1} value={w.qty || 1} onChange={(event) => update((current) => updateWire(current, w.idx, { qty: parseDecimal_(event.target.value) || 1 }))} /></label>
            {(cores > 1 || !!w.cores) && <>
              <label className="field-label">Жил<input type="number" min={1} value={cores} onChange={(event) => update((current) => updateWire(current, w.idx, { cores: parseDecimal_(event.target.value) || 1 }))} /></label>
              <label className="field-label">Разделка, мм<input type="number" min={1} value={w.jacket || JACKET_STRIP_MM_} onChange={(event) => update((current) => updateWire(current, w.idx, { jacket: event.target.value.trim() }))} /></label>
            </>}
            <button className="btn-remove" title="Удалить" onClick={() => update((current) => ({ ...current, wires: current.wires.filter((x) => x.idx !== w.idx) }))}>×</button>
          </div>;
        })}
        <button className="btn-add" onClick={() => update((current) => ({ ...current, wires: [...current.wires, { idx: current.nextWireIdx, compId: "", length: 0, qty: 1 }], nextWireIdx: current.nextWireIdx + 1 }))}>+ Добавить кабель</button>
      </div>

      {(["A", "B"] as Side[]).map((side) => <div key={side}>
        <h3>Сторона {sideRu_(side)} — разъёмы</h3>
        {gen.endpointsOnSide_(side).length ? gen.endpointsOnSide_(side).map(endpointCard) : <div className="empty small">Разъёмов нет — добавьте кнопкой ниже</div>}
        {complex && <button className="btn-add" onClick={() => update((current) => addEndpoint(current, side))}>+ Разъём (сторона {sideRu_(side)})</button>}
      </div>)}

      {active.some(gen.isJacketedCable_) && <label className="toggle-row"><input type="checkbox" checked={S.rotor} onChange={(event) => update({ rotor: event.target.checked })} /> Роторный станок — многожильный кабель разделывается при резке (оболочка и жилы)</label>}
      {crimpSides.length > 0 && <label className="toggle-row"><input type="checkbox" checked={gen.crimpCutEnabled_()} onChange={(event) => update({ crimpCut: event.target.checked, crimpCutManual: true })} /> Обжим на автомате при резке (CUT_WIRE+1crimp) — сторона
        <select aria-label="Сторона обжима при резке" value={gen.crimpCutSideSelected_()} onChange={(event) => update({ crimpCutSide: event.target.value })}>{crimpSides.map((s) => <option key={s} value={s}>{sideRu_(s)}</option>)}</select>
        <small className="muted">проводов в партии: {gen.batchWires_()}{S.crimpCutManual ? "" : " — по умолчанию вкл при > 300"}</small></label>}

      <label className="toggle-row"><input type="checkbox" checked={S.twist.enabled} onChange={(event) => update((current) => {
        const on = event.target.checked;
        const auto = makeGen(D, current).autoDetectTwistPairs();
        const pairs = on && !current.twist.pairs.length ? (auto.length ? auto.map(([a, b]) => ({ a: String(a), b: String(b) })) : [{ a: "", b: "" }]) : current.twist.pairs;
        return { ...current, twist: { ...current.twist, enabled: on, pairs } };
      })} /> Свивка (TWIST)</label>
      {S.twist.enabled && <div className="gcard">
        <div className="radio-row">
          <label><input type="radio" checked={S.twist.mode === "pairs"} onChange={() => update((current) => ({ ...current, twist: { ...current.twist, mode: "pairs" } }))} /> Парами</label>
          <label><input type="radio" checked={S.twist.mode === "all"} onChange={() => update((current) => ({ ...current, twist: { ...current.twist, mode: "all" } }))} /> Все вместе</label>
        </div>
        {S.twist.mode === "pairs" ? <div>
          <span className="field-label">Пары для свивки</span>
          {S.twist.pairs.map((pair, i) => <div className="twist-pair-row" key={i}>
            {(["a", "b"] as const).map((key) => <select key={key} aria-label={`Пара ${i + 1} ${key}`} value={pair[key]} onChange={(event) => update((current) => ({ ...current, twist: { ...current.twist, pairs: current.twist.pairs.map((p, j) => (j === i ? { ...p, [key]: event.target.value } : p)) } }))}>
              <option value="">—</option>{active.map((w, pos) => <option key={w.idx} value={pos}>{compLabel(w.compId)}</option>)}</select>)}
            <button className="btn-remove" onClick={() => update((current) => ({ ...current, twist: { ...current.twist, pairs: current.twist.pairs.filter((_, j) => j !== i) } }))}>×</button>
          </div>)}
          <button className="btn-add" onClick={() => update((current) => ({ ...current, twist: { ...current.twist, pairs: [...current.twist.pairs, { a: "", b: "" }] } }))}>+ Добавить пару</button>
          <button className="btn-add" title="Определить пары по запасу длины из спецификации" onClick={() => update((current) => {
            const auto = makeGen(D, current).autoDetectTwistPairs();
            return { ...current, twist: { ...current.twist, pairs: auto.length ? auto.map(([a, b]) => ({ a: String(a), b: String(b) })) : [{ a: "", b: "" }] } };
          })}>Авто</button>
        </div> : <div className="chip-row">{active.map((w, pos) => <label key={w.idx}><input type="checkbox" checked={gen.twistWireIndices_().includes(pos)} onChange={() => update((current) => {
          const now = makeGen(D, current).twistWireIndices_();
          return { ...current, twist: { ...current.twist, wires: now.includes(pos) ? now.filter((i) => i !== pos) : [...now, pos] } };
        })} /> {compLabel(w.compId)}</label>)}</div>}
        <div className="ter-row">
          <label className="field-label">Шаг свивки, мм<input aria-label="Шаг свивки" value={S.twist.pitch} placeholder="—" onChange={(event) => twistPitchInput(event.target.value)} /></label>
          <label className="field-label">Z₀, Ω<input aria-label="Z0" value={S.twist.z0} placeholder="по шагу / задать" onChange={(event) => twistZ0Input(event.target.value)} /></label>
          <label className="field-label">Ø провода, мм<input aria-label="Ø провода" className={gen.twistDiaValue().trim() ? "f-ok" : "f-empty"} value={gen.twistDiaValue()} placeholder="авто из СПР.КАБ" onChange={(event) => update((current) => ({ ...current, twist: { ...current.twist, dia: event.target.value, diaManual: !!event.target.value.trim() } }))} /></label>
        </div>
        {note.text && <small className={note.explicit ? "muted" : "danger-text"}>{note.text}{note.warn && <><br /><span className="danger-text">⚠ {note.warn}</span></>}</small>}
      </div>}

      {complex && <>
        <label className="toggle-row"><input type="checkbox" checked={S.spl.enabled} onChange={(event) => update((current) => ({ ...current, spl: { ...current.spl, enabled: event.target.checked } }))} /> Сростки (Spl) — соединение проводов в стволе</label>
        {S.spl.enabled && <div className="gcard"><div className="ter-row">
          <label className="field-label">Сростков на изделие<input type="number" min={1} value={S.spl.count} onChange={(event) => update((current) => ({ ...current, spl: { ...current.spl, count: event.target.value } }))} /></label>
          <label className="field-label">Проводов в сростке (разные — через запятую)<input value={S.spl.wires} placeholder="3 или 4, 2, 2" onChange={(event) => update((current) => ({ ...current, spl: { ...current.spl, wires: event.target.value } }))} /></label>
          <label className="field-label">Изоляция сростка — длина трубки, мм<input value={S.spl.tut} placeholder="напр. 40" onChange={(event) => update((current) => ({ ...current, spl: { ...current.spl, tut: event.target.value } }))} /></label>
          <label className="field-label">Трубка сростка из состава{selectComps(capTubes, S.spl.tutMat, (id) => update((current) => ({ ...current, spl: { ...current.spl, tutMat: id } })), "Трубка сростка")}</label>
          <label className="field-label">Зачистка под сросток, мм<input value={S.spl.strip} placeholder="по КД" onChange={(event) => update((current) => ({ ...current, spl: { ...current.spl, strip: event.target.value } }))} /></label>
        </div>
          <label className="ep-otp"><input type="checkbox" checked={S.spl.fold} onChange={(event) => update((current) => ({ ...current, spl: { ...current.spl, fold: event.target.checked } }))} /> Складывание жилы перед установкой в гильзу (663К.1 КБ4: ГС 2,5, «Схема увеличения сечения»)</label>
        </div>}
      </>}

      <label className="toggle-row"><input type="checkbox" checked={S.hs.enabled} onChange={(event) => update((current) => ({ ...current, hs: { ...current.hs, enabled: event.target.checked, manual: true } }))} /> Термоусадка на жгут — концы оплётки (ТТК/ТУТ из состава)</label>
      {S.hs.enabled && <div className="gcard">{hsComps.map(({ row, comp }, i) => <div className="ter-row" key={row.compId}>
        <label className="field-label">{comp ? comp.art || comp.name : row.compId} — длина отрезка, мм<input value={row.len} onChange={(event) => update((current) => ({ ...current, hs: { ...current.hs, rows: current.hs.rows.map((r, j) => (j === i ? { ...r, len: event.target.value } : r)) } }))} /></label>
        <label className="field-label">Отрезков на изделие<input value={row.cnt} onChange={(event) => update((current) => ({ ...current, hs: { ...current.hs, rows: current.hs.rows.map((r, j) => (j === i ? { ...r, cnt: event.target.value } : r)) } }))} /></label>
      </div>)}</div>}
      <label className="toggle-row"><input type="checkbox" checked={S.mark} onChange={(event) => update({ mark: event.target.checked })} /> Маркировка жгута (бирки из состава)</label>
      {S.mark && <div className="gcard"><small>{marks.map((m) => `${m.art || m.name} × ${m.qty}`).join("; ")}</small></div>}
      <label className="toggle-row"><input type="checkbox" checked={S.freeStrip.enabled} onChange={(event) => update((current) => ({ ...current, freeStrip: { ...current.freeStrip, enabled: event.target.checked } }))} /> Зачистка свободных концов (без стороны В, без лужения)</label>
      {S.freeStrip.enabled && <div className="gcard"><label className="field-label">Длина зачистки, мм<input value={S.freeStrip.len} placeholder="по КД" onChange={(event) => update((current) => ({ ...current, freeStrip: { ...current.freeStrip, len: event.target.value } }))} /></label></div>}
      {complex && <>
        <label className="toggle-row"><input type="checkbox" checked={S.cap.enabled} onChange={(event) => update((current) => ({ ...current, cap: { ...current.cap, enabled: event.target.checked } }))} /> Заделка свободных концов — жилы, не заведённые в разъём своей стороны (INS_END_CAP)</label>
        {S.cap.enabled && <div className="gcard"><div className="ter-row">
          <label className="field-label">Трубка из состава{selectComps(capTubes, S.cap.tut, (id) => update((current) => ({ ...current, cap: { ...current.cap, tut: id } })), "Трубка заделки")}</label>
          <label className="field-label">Отрезок на конец, мм<input value={S.cap.len} placeholder="из нормы" onChange={(event) => update((current) => ({ ...current, cap: { ...current.cap, len: event.target.value } }))} /></label>
        </div></div>}
      </>}

      <label className="toggle-row"><input type="checkbox" checked={S.wrap.enabled} onChange={(event) => update((current) => syncHsWithBraid_(D, { ...current, wrap: { ...current.wrap, enabled: event.target.checked } }))} /> Лента / оплётка на жгут (INS_WRAP)</label>
      {S.wrap.enabled && <div className="gcard">{S.wrap.slots.map((slot, i) => (i === 0 || complex) && <div className="ter-row" key={WRAP_SLOTS_[i] || "1"}>
        <label className="field-label">{["Материал из состава", "Второй материал (необязательно)", "Третий материал (необязательно)", "Четвёртый материал (необязательно)"][i]}
          {selectComps(wrapOpts, slot.mat, (id) => update((current) => {
            const c = makeGen(D, current).getCompById(id);
            const g = makeGen(D, current);
            const slots = current.wrap.slots.map((s, j) => (j === i ? { mat: id, kind: c ? g.guessWrapKind_(c) : s.kind, len: g.prefillWrapLen_(c) } : s));
            return syncHsWithBraid_(D, { ...current, wrap: { ...current.wrap, slots } });
          }), `Материал ${i + 1}`)}</label>
        <label className="field-label">Вид<select value={slot.kind} onChange={(event) => update((current) => syncHsWithBraid_(D, { ...current, wrap: { ...current.wrap, slots: current.wrap.slots.map((s, j) => (j === i ? { ...s, kind: event.target.value as WrapKind } : s)) } }))}>
          {WRAP_KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="field-label">Длина на изделие, мм<input value={slot.len} placeholder="по КД" onChange={(event) => update((current) => ({ ...current, wrap: { ...current.wrap, slots: current.wrap.slots.map((s, j) => (j === i ? { ...s, len: event.target.value } : s)) } }))} /></label>
      </div>)}</div>}

      {complex && <>
        <label className="toggle-row"><input type="checkbox" checked={S.seal.enabled} onChange={(event) => update((current) => ({ ...current, seal: { ...current.seal, enabled: event.target.checked } }))} /> Герметизация (SEAL) — клей-герметик на пайку и хвостовики разъёмов</label>
        {S.seal.enabled && <div className="gcard"><div className="ter-row">
          <label className="field-label">Герметик из состава{selectComps(gen.sealComps(), S.seal.mat, (id) => update((current) => ({ ...current, seal: { ...current.seal, mat: id } })), "Герметик")}</label>
          <label className="field-label">Мест на изделие<input value={S.seal.places} placeholder="по КД" onChange={(event) => update((current) => ({ ...current, seal: { ...current.seal, places: event.target.value } }))} /></label>
        </div></div>}
      </>}

      {!hasB && <>
        <label className="toggle-row"><input type="checkbox" checked={S.tin.enabled} onChange={(event) => update((current) => ({ ...current, tin: { ...current.tin, enabled: event.target.checked } }))} /> Лужение (TIN) — лужение свободных концов</label>
        {S.tin.enabled && <div className="gcard">
          <span className="field-label">Какие концы лудить (по умолчанию все)</span>
          <div className="chip-row">{active.map((w, pos) => <label key={w.idx}><input type="checkbox" checked={gen.tinWireIndices_().includes(pos)} onChange={() => update((current) => {
            const now = makeGen(D, current).tinWireIndices_();
            return { ...current, tin: { ...current.tin, wires: now.includes(pos) ? now.filter((i) => i !== pos) : [...now, pos] } };
          })} /> {compLabel(w.compId)}</label>)}</div>
          <label className="field-label">Длина зачистки под лужение, мм<input value={S.tin.stripLen} placeholder="—" onChange={(event) => update((current) => ({ ...current, tin: { ...current.tin, stripLen: event.target.value } }))} /></label>
        </div>}
      </>}
    </div>}

    {S.tab === "coax" && <div className="tab-coax">
      <h3>Коакс-кабель</h3>
      <div className="gcard">
        <label className="field-label">Длина кабеля по чертежу, мм<input aria-label="Длина коакс-кабеля" type="number" min={1} value={S.coax.len} placeholder="—" onChange={(event) => update((current) => ({ ...current, coax: { ...current.coax, len: event.target.value } }))} /></label>
        <label className="toggle-row"><input type="checkbox" checked={S.coax.tutEnabled} onChange={(event) => update((current) => ({ ...current, coax: { ...current.coax, tutEnabled: event.target.checked } }))} /> ТУТ на концах</label>
        <label className="field-label">Длина ТУТ, мм<input type="number" min={1} value={S.coax.tutLen} placeholder="—" onChange={(event) => update((current) => ({ ...current, coax: { ...current.coax, tutLen: event.target.value } }))} /></label>
      </div>
      <h3>Сторона А — разъём (из BOM)</h3>
      {coaxSideBlock("A")}
      {S.coax.sideB && <><h3>Сторона B — разъём (из BOM)</h3>{coaxSideBlock("B")}</>}
      {!S.coax.sideB && <label className="toggle-row"><input type="checkbox" checked={S.coax.stripB} onChange={(event) => update((current) => ({ ...current, coax: { ...current.coax, stripB: event.target.checked } }))} /> Зачистка стороны B (голый конец кабеля)</label>}
      <h3>Экран</h3>
      <div className="gcard"><label className="field-label">Тип экрана (на сборку)<select aria-label="Тип экрана" value={S.coax.shield} onChange={(event) => update((current) => ({ ...current, coax: { ...current.coax, shield: event.target.value } }))}>
        <option value="обжим">обжим (опрессовка втулки)</option><option value="пайка">пайка экрана</option>
      </select></label></div>
    </div>}

    <h3>Операции</h3>
    <div className="gcard ops-card">
      {!ops.length && <div className="op-empty">{S.tab === "coax" ? "Выберите разъём — появятся операции" : "Добавьте кабель — появятся операции"}</div>}
      {ops.map((spec, i) => {
        const row = gen.opRow(spec);
        const setTime = (key: "tPrep" | "tOp" | "tMachine", value: string) => setS((current) => ({ ...current, times: { ...current.times, [spec.opKey]: { ...row.times, [key]: value } } }));
        return <div className="op-item" key={spec.opKey}>
          <div className="op-badge">{i + 1}</div>
          <div>
            <div className="op-title">{spec.label}</div>
            <label className="field-label">Шаблон (_TC_LIBRARY)<select aria-label={`Шаблон: ${spec.label}`} className={row.templateId ? (row.matched ? "f-ok" : "f-warn") : "f-empty"} value={row.templateId}
              onChange={(event) => update((current) => ({ ...current, selectedTemplates: { ...current.selectedTemplates, [spec.opKey]: event.target.value }, manualTemplates: current.manualTemplates.includes(spec.opKey) ? current.manualTemplates : [...current.manualTemplates, spec.opKey] }))}>
              <option value="">— не выбран —</option>
              {D.templates.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
            </select></label>
            <small className={row.matched ? "op-match found" : "op-match not-found"}>{row.info}</small>
            <div className="op-times">
              {([["tPrep", "Вр.подг, сек"], ["tOp", "Вр.оп, сек"], ["tMachine", "Вр.маш, сек"]] as const).map(([key, label]) =>
                <label key={key} className="field-label">{label}<input type="number" min={0} placeholder="0" className={row.matched ? "f-ok" : row.times[key] ? "f-warn" : "f-empty"} value={row.times[key]} onChange={(event) => setTime(key, event.target.value)} /></label>)}
            </div>
          </div>
        </div>;
      })}
    </div>

    <label className="toggle-row"><input type="checkbox" checked={S.materialsSheet} onChange={(event) => update({ materialsSheet: event.target.checked })} /> Свод материалов (отдельный лист)</label>
    <div className="form-actions">
      <button className="secondary" disabled={busy || !card.document} onClick={() => void deleteGenerated()}>Удалить созданные</button>
      <button className="primary" disabled={busy} onClick={() => void generate()}>Создать техкарты</button>
    </div>
    {status.text && <div className={status.error ? "error-banner" : "muted"} role={status.error ? "alert" : undefined}>{status.text}</div>}
  </div>;
}
