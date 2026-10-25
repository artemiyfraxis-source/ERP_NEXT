import type { ReactNode } from "react";
import { UnitInput } from "./GenControls";
import { wrapFixText, wrapSegsTotal, type WrapFix, type WrapKind, type WrapSlot } from "./tcGenLogic";
import "./wrapTable.css";

/* «Лента, оплётка, рукава» (владелец 25.10: «ленты — не всё удобно настраивается»): слои жгута одной таблицей — что надеть,
   как, где (весь ствол или ветви), сколько и как закрепить (сплошь, точками с шагом, спиралью по участкам). Отрезки на ветви
   — здесь же, под слоем с оплёткой. Логика генератора — tcGenLogic.ts (insWrap), состояние формы — `S.wrap`. */

export type WrapBranch = { id: string; name: string; /** Ключ разъёма «А1» — в подписи поля (как у остальных полей карточки разъёма). */ key: string; own: string; placeholder: string };

const KINDS: Array<[WrapKind, string]> = [
  ["tape", "Лента поверх"], ["under", "Лента под оплётку"], ["braid", "Оплётка, рукав"],
];
const FIXES: Array<[WrapFix | "", string]> = [["", "Сплошная намотка"], ["spots", "Точками с шагом"], ["spiral", "Спиралью по участкам"]];

export function WrapTable({ slots, visible, materials, branches, allOwn, summary, plan, onMat, onKind, onLen, onFix, onBranchLen }: {
  slots: WrapSlot[];
  /** Сколько слоёв показано: выбранные плюс один пустой (до четырёх). */
  visible: number;
  materials: Array<{ id: string; label: string }>;
  /** Ветви с оплёткой (стороны А «Сложной сборки»). */
  branches: WrapBranch[];
  /** У всех ветвей свой отрезок — «Длина на изделие» оплётки ни на что не влияет. */
  allOwn: boolean;
  /** «Отрезки по ветвям: XS1 440 · XS2 340 мм, всего 780 мм.» */
  summary: string;
  /** Порядок надевания по слоям, как его строит генератор. */
  plan: string[];
  onMat: (i: number, id: string) => void;
  onKind: (i: number, kind: WrapKind) => void;
  onLen: (i: number, len: string) => void;
  onFix: (i: number, patch: Partial<Pick<WrapSlot, "fix" | "points" | "step" | "segs">>) => void;
  onBranchLen: (epId: string, len: string) => void;
}) {
  const hasBraid = slots.some((slot) => slot.mat && slot.kind === "braid");
  const firstBraid = slots.findIndex((slot) => slot.mat && slot.kind === "braid");
  const branchLens = (): ReactNode => <div className="wt-branches" role="group" aria-label="Отрезки оплётки по ветвям">
    <span className="wt-where">Ветви</span>
    {branches.map((b) => <label className="wt-branch" key={b.id}>
      <span>{b.name}</span>
      <UnitInput unit="мм" autoComplete="off" aria-label={`Оплётка ветви ${b.key}, отрезок`} value={b.own} placeholder={b.placeholder || "по КД"} onChange={(event) => onBranchLen(b.id, event.target.value)} />
    </label>)}
    <small className="wt-hint">«0» — ветвь без оплётки</small>
  </div>;
  return <div className="wt" role="group" aria-label="Слои ленты и оплётки">
    <div className="wt-head" aria-hidden="true"><span>№</span><span>Материал из состава</span><span>Как надеть</span><span>Где</span><span>Длина</span></div>
    {slots.slice(0, visible).map((slot, i) => {
      const braid = slot.kind === "braid";
      const filled = !!slot.mat;
      return <div className="wt-layer" key={i}>
        <div className="wt-row">
          <span className="wt-n">{i + 1}</span>
          <select aria-label={`Материал ${i + 1}`} value={slot.mat} onChange={(event) => onMat(i, event.target.value)}>
            <option value="">{i ? "— ещё слой —" : "— не выбран —"}</option>
            {materials.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </select>
          {filled ? <select aria-label="Как надеть" title="Лента поверх — последней операцией; под оплётку — первым слоем; оплётка и рукав — до обжима контактов" value={slot.kind} onChange={(event) => onKind(i, event.target.value as WrapKind)}>
            {KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select> : <span />}
          {filled ? <span className="wt-where">{braid ? (branches.length ? `ветви: ${branches.length}` : "ветви") : "весь ствол"}</span> : <span />}
          {filled ? (braid && branches.length && allOwn
            ? <span className="wt-by" title="Длина — по отрезкам ветвей">по ветвям</span>
            : <UnitInput unit="мм" autoComplete="off" aria-label="Длина на изделие" value={slot.len} placeholder="по КД" onChange={(event) => onLen(i, event.target.value)} />) : <span />}
        </div>
        {filled && !braid && <div className="wt-fix">
          <select aria-label={`Закрепление слоя ${i + 1}`} value={slot.fix || ""} onChange={(event) => onFix(i, { fix: (event.target.value || undefined) as WrapFix | undefined })}>
            {FIXES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
          {slot.fix === "spots" && <>
            <UnitInput unit="шт" autoComplete="off" aria-label={`Точек фиксации слоя ${i + 1}`} inputMode="numeric" value={slot.points || ""} placeholder="8" onChange={(event) => onFix(i, { points: event.target.value })} />
            <UnitInput unit="мм" autoComplete="off" aria-label={`Шаг точек слоя ${i + 1}`} inputMode="numeric" value={slot.step || ""} placeholder="шаг" onChange={(event) => onFix(i, { step: event.target.value })} />
          </>}
          {slot.fix === "spiral" && <>
            <input className="wt-segs" autoComplete="off" spellCheck={false} aria-label={`Участки спирали слоя ${i + 1}, мм`} value={slot.segs || ""} placeholder="40, 40, 140…" onChange={(event) => onFix(i, { segs: event.target.value })} />
            <small className="wt-hint">{wrapSegsTotal(slot.segs) ? `всего ${wrapSegsTotal(slot.segs)} мм` : "длины участков по порядку, мм"}</small>
          </>}
          {slot.fix && wrapFixText(slot) && <small className="wt-hint" title="Так операция называется в маршруте">{wrapFixText(slot)}</small>}
        </div>}
        {i === firstBraid && hasBraid && branches.length > 0 && branchLens()}
      </div>;
    })}
    {summary && hasBraid && <p className="opt-note wt-summary">{summary}</p>}
    {plan.length > 0 && <ol className="wrap-plan" aria-label="Порядок в маршруте">{plan.map((line) => <li key={line}>{line}</li>)}</ol>}
  </div>;
}
