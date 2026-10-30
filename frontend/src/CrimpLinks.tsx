import { useCallback, useEffect, useState } from "react";
import {
  failureText, referencesApi, techcardsApi,
  type ConnectorSeries, type CrimpOverview, type CrimpTarget, type CrimpTerminal, type TechOperationRecord,
} from "./api";
import { CODE_KEY, TOOL_KEY, invOf, nameOf, pick, type EquipmentModel } from "./equipmentModel";

/** «Что обжимает» — one block for a crimp tool and a matrix (owner 12.10.2026): the terminals and lugs that name the
    tool's code in БД.ТЕР «Аппликатор», and the connector series listed on the tool record itself. */

const terminalTitle = (item: Pick<CrimpTerminal, "article" | "series" | "maker" | "kind">) =>
  [item.maker, item.series, item.article].filter(Boolean).join(" · ") || "—";
const terminalOf = (record: TechOperationRecord): CrimpTerminal => {
  const f = record.fields;
  return {
    id: record.id, display_text: record.display_text,
    article: pick(f, "terArticle", "Артикул контакта (REEL)", "Артикул\nконтакта (REEL)"),
    series: pick(f, "terSeries", "Series"), maker: pick(f, "terManufacturer", "Производитель"),
    kind: pick(f, "Тип разъема", "Тип\nразъема", "connType"), applicator: pick(f, "terApplicator", "Аппликатор"), doubtful: false,
  };
};

export function CrimpLinks({ model, tool, siblings, canEdit, onChanged, onEditTool }: {
  model: EquipmentModel;
  tool: TechOperationRecord;
  siblings: TechOperationRecord[];
  canEdit: boolean;
  onChanged: () => void;
  onEditTool: (record: TechOperationRecord) => void;
}) {
  const code = pick(tool.fields, CODE_KEY).toUpperCase();
  const [overview, setOverview] = useState<CrimpOverview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<CrimpTerminal[]>([]);
  const [series, setSeries] = useState<ConnectorSeries[] | null>(null);
  const [seriesQuery, setSeriesQuery] = useState("");
  const forTool = pick(tool.fields, TOOL_KEY);
  const machine = forTool ? siblings.find((item) => invOf(item) === forTool) : undefined;

  const load = useCallback(() => {
    referencesApi.crimps(tool.id).then(setOverview).catch((reason) => setError(failureText(reason, "Связи не загрузились")));
  }, [tool.id]);
  useEffect(() => { setOverview(null); setError(""); setQuery(""); setFound([]); load(); }, [load]);

  useEffect(() => {
    const text = query.trim();
    if (text.length < 2) { setFound([]); return; }
    let live = true;
    const timer = window.setTimeout(() => {
      // Контакты и наконечники (базы «Терминалы» и «Наконечники» листа БД.ТЕР): аппликатор обжимает и те, и другие.
      referencesApi.techOperations("ter", text, 40, true).then((result) => {
        if (live) setFound(result.records.map(terminalOf));
      }).catch(() => { if (live) setFound([]); });
    }, 250);
    return () => { live = false; window.clearTimeout(timer); };
  }, [query]);

  async function run(action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true); setError("");
    try { await action(); load(); onChanged(); } catch (reason) { setError(failureText(reason, "Не записано")); } finally { setBusy(false); }
  }
  const linkedIds = new Set((overview?.terminals ?? []).map((item) => item.id));
  const targets = overview?.targets ?? [];
  const loadSeries = () => {
    if (series === null) techcardsApi.connectorSeries().then((result) => setSeries(result.series)).catch(() => setSeries([]));
  };
  const seriesHits = seriesQuery.trim().length < 2 || !series ? [] : series.filter((item) =>
    `${item.title} ${item.maker}`.toLocaleLowerCase("ru-RU").includes(seriesQuery.trim().toLocaleLowerCase("ru-RU"))
    && !targets.some((target) => target.kind === "series" && target.key === item.id)).slice(0, 8);

  return <div className="tr-block eq-crimp">
    <h4>Что обжимает <span>{overview ? overview.terminals.length + targets.length : "…"}</span></h4>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <p className="eq-line">Код в базе терминалов: {code ? <b className="tnum">{code}</b> : <span className="muted">не задан</span>}
      {!code && canEdit && <> <button type="button" className="secondary small" onClick={() => onEditTool(tool)}>Задать код</button></>}
      {machine && <> · для инструмента <b>{invOf(machine)} {nameOf(machine)}</b></>}</p>
    {code && <>
      {overview && overview.terminals.length === 0 && <div className="empty">Ни один контакт не указывает код {code} в «Аппликатор».</div>}
      {overview && overview.terminals.length > 0 && <table className="eq-table"><thead><tr><th>Контакт, клемма</th><th>Тип</th><th>Аппликатор в БД.ТЕР</th><th /></tr></thead>
        <tbody>{overview.terminals.map((item) => <tr key={item.id}>
          <td>{terminalTitle(item)}</td><td>{item.kind || "—"}</td>
          <td className="tnum">{item.applicator}{item.doubtful && <span className="tone-warn eq-doubt" title="Код стоит под вопросом (например, «возможно S12») — подтвердите его в справочнике терминалов"> под вопросом</span>}</td>
          <td className="eq-act">{canEdit && !item.doubtful && <button type="button" className="secondary small" disabled={busy}
            onClick={() => void run(() => referencesApi.crimpTerminal(tool.id, item.id, false))}>Убрать</button>}</td>
        </tr>)}</tbody></table>}
      {canEdit && <div className="eq-add">
        <input type="search" aria-label="Найти контакт или клемму" placeholder="Добавить контакт: артикул, серия, производитель, тип (ТМЛ, НШВИ)…" value={query} onChange={(event) => setQuery(event.target.value)} />
        {found.filter((item) => !linkedIds.has(item.id)).slice(0, 12).map((item) => <div className="eq-hit" key={item.id}>
          <span>{terminalTitle(item)} <span className="muted">{item.kind}{item.applicator ? ` · ${item.applicator}` : ""}</span></span>
          <button type="button" className="secondary small" disabled={busy} onClick={() => void run(() => referencesApi.crimpTerminal(tool.id, item.id, true))}>Добавить</button>
        </div>)}
        {query.trim().length >= 2 && found.length > 0 && found.every((item) => linkedIds.has(item.id)) && <div className="empty">Все найденные уже в списке.</div>}
      </div>}
    </>}
    <h5>Серии разъёмов</h5>
    {targets.length === 0 ? <div className="empty">Серии разъёмов не указаны.</div> : <div className="tr-chips">{targets.map((target) => <span key={`${target.kind}:${target.key}`} className="tr-code eq-op">
      {target.label}{canEdit && <button type="button" className="eq-x" aria-label={`Убрать ${target.label}`} onClick={() => void run(() => referencesApi.crimpTarget(tool.id, target, false))}>×</button>}</span>)}</div>}
    {canEdit && <div className="eq-add">
      <input type="search" aria-label="Найти серию разъёмов" placeholder="Добавить серию разъёмов: название или производитель…" value={seriesQuery}
        onFocus={loadSeries} onChange={(event) => { loadSeries(); setSeriesQuery(event.target.value); }} />
      {seriesHits.map((item) => {
        const target: CrimpTarget = { kind: "series", key: item.id, label: `${item.maker ? `${item.maker} ` : ""}${item.title}` };
        return <div className="eq-hit" key={item.id}><span>{target.label}</span>
          <button type="button" className="secondary small" disabled={busy} onClick={() => void run(() => referencesApi.crimpTarget(tool.id, target, true))}>Добавить</button></div>;
      })}
    </div>}
    {model.items.length > 1 && <p className="muted">Код и связи хранятся у записи {invOf(tool) || nameOf(tool)}.</p>}
  </div>;
}
