import { useEffect, useRef, useState, type ReactNode } from "react";
import { failureText, type ProjectTechcard } from "./api";
import { EtkRouteDiagram } from "./EtkRouteDiagram";
import { etkDocOperations, etkTitle, harnessPins, navSub, type EtkDocOperation } from "./etkDocOps";
import { etkConfigOp, etkPfArt } from "./etkPfArt";
import { etkRouteChips, etkRouteOf, formatMinutes, type EtkPfGroup } from "./etkRoute";
import { PageWindow } from "./PageWindow";
import { pluralRu } from "./tcGenLogic";

/* Схема полуфабрикатов генератора — как в собранной ЭТК (владелец 22.10: «нет до сих пор нормальной схемы
   полуфабрикатов как на 713К»). Сервер собирает ЭТК из формы так же, как «Создать», но ничего не пишет
   (POST …/build/preview); схема рисуется тем же EtkRouteDiagram, что в техкарте: ПФ по уровням, входы, эскиз,
   операции по номерам, выход. Форма меняется — схема перестраивается через ~0,6 с; прежняя видна, пока строится новая. */

export type BuildPreview = (config: Record<string, unknown>) => Promise<ProjectTechcard>;

export type PreviewState = { card: ProjectTechcard | null; loading: boolean; error: string };

/** Пауза после правки формы, мс: схема не строится на каждое нажатие клавиши. */
export const PREVIEW_DELAY = 600;

/** Схема по конфигурации формы: строится через `delay` мс после последней правки, ответ на старую правку не
 *  перекрывает новую. `config` null — форма ещё не собирается: запроса нет, последняя схема остаётся. */
export function useBuildPreview(config: Record<string, unknown> | null, load: BuildPreview | undefined, delay = PREVIEW_DELAY): PreviewState {
  const [state, setState] = useState<PreviewState>({ card: null, loading: false, error: "" });
  const latest = useRef(0);
  const key = config && load ? JSON.stringify(config) : "";
  // Тело запроса — конфигурация последней правки (эффект зависит от её JSON, не от объекта).
  const sent = useRef(config);
  sent.current = config;
  useEffect(() => {
    const ticket = ++latest.current;
    if (!key || !load) {
      setState((current) => (current.loading ? { ...current, loading: false } : current));
      return undefined;
    }
    // «Строим схему…» сразу: пока длится пауза, видна прежняя схема с этой пометкой.
    setState((current) => (current.loading ? current : { ...current, loading: true }));
    const timer = window.setTimeout(() => {
      const body = sent.current;
      if (!body) return;
      load(body).then(
        (card) => { if (ticket === latest.current) setState({ card, loading: false, error: "" }); },
        (reason) => { if (ticket === latest.current) setState((current) => ({ ...current, loading: false, error: failureText(reason, "Схема не построена") })); },
      );
    }, delay);
    return () => window.clearTimeout(timer);
  }, [key, load, delay]);
  useEffect(() => () => { latest.current += 1; }, []);
  return state;
}

/** Итог схемы: «4 ПФ · 9 операций · на изделие 12,5 мин». */
export function previewSummary(card: ProjectTechcard): ReactNode {
  const route = etkRouteOf(card.document);
  const operations = etkDocOperations(card.document).length;
  const blocks = route?.blocks.length ?? 0;
  const final = route ? etkRouteChips(route).find((chip) => chip.final) : undefined;
  // Норма меньше сотой минуты на изделие — не «0 мин»: время ещё не задано.
  const minutes = final && final.rolledMinutes > 0 ? formatMinutes(final.rolledMinutes) : "";
  return <>{blocks} ПФ · {operations} {pluralRu(operations, "операция", "операции", "операций")}
    {minutes && minutes !== "0" && <> · на изделие <strong>{minutes} мин</strong></>}</>;
}

/** Схема ПФ собранной из формы ЭТК: состояние запроса и сама схема. Схемы ещё нет и она не строится — null. */
export function GenPfScheme({ preview }: { preview: PreviewState }) {
  const { card, loading, error } = preview;
  const [current, setCurrent] = useState(0);
  const [wide, setWide] = useState(false);
  const document = card?.document ?? null;
  const route = etkRouteOf(document);
  const list = etkDocOperations(document);
  useEffect(() => { if (current > list.length) setCurrent(0); }, [current, list.length]);
  if (!card && !loading && !error) return null;
  const batch = Number(String((document?.kit as { part_quantity?: unknown } | undefined)?.part_quantity ?? "").replace(",", ".")) || 1;
  const configOp = etkConfigOp(document as Record<string, unknown> | null);
  const title = (item: EtkDocOperation) => String(configOp(item as unknown as Record<string, unknown>).label ?? "").trim() || etkTitle(item);
  const sub = (item: EtkDocOperation) => navSub(item);
  const pins = route ? harnessPins(list) : {};
  const art = (group: EtkPfGroup, inputs: string[]) => etkPfArt({
    group, inputs, document: document as Record<string, unknown> | null, pins, batch,
    operations: group.ops.map((position) => list[position - 1]).filter(Boolean) as unknown as Array<Record<string, unknown>>,
  });
  // Щелчок по операции раскрывает её ПФ (шире, эскиз крупнее, коды карт); второй щелчок — сворачивает.
  const pick = (position: number) => setCurrent((open) => (open === position ? 0 : position));
  const diagram = (actions?: ReactNode) => route && <EtkRouteDiagram route={route} list={list} batch={batch} current={current}
    onPick={pick} title={title} sub={sub} art={art} heading={false} actions={actions} />;
  return <div className="gen-pf-scheme" aria-busy={loading}>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {loading && <div className={card ? "gen-pf-status" : "empty"} role="status">Строим схему…</div>}
    {card && !route && !loading && <div className="empty">В собранной ЭТК нет схемы ПФ.</div>}
    {diagram(<button type="button" className="secondary small" onClick={() => setWide(true)}>Во весь экран</button>)}
    {wide && card && route && <PageWindow label="Схема полуфабрикатов" width={2400} dismissable close={() => setWide(false)}>
      <div className="panel gen-pf-window">
        <div className="gen-pf-window-head"><h2>Схема полуфабрикатов</h2><span className="gen-pf-window-sum">{previewSummary(card)}</span>
          <button type="button" className="secondary small" onClick={() => setWide(false)}>Закрыть</button></div>
        {diagram()}
      </div>
    </PageWindow>}
  </div>;
}
