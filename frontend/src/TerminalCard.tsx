import { PageWindow } from "./PageWindow";
import { TER_L_MINUS_HINT, TER_L_PLUS_HINT } from "./referenceLabels";
import type { TechOperationRecord } from "./api";

/** «База данных → Терминалы»: карточка контакта по протоколу v5.2 (владелец 12–13.10.2026) — справа от списка, как
    карточка станка. Пол, артикулы REEL и BAG, материал / покрытие, ток и даташит; обжим проводника и изоляции, диаметр
    изоляции, усилие обрыва, зачистка. Значения приходят с сервера (`card` записи — так же, как их читает генератор ТК);
    чего нет в спецификации производителя, то «—», ничего не досчитывается. */

export interface TerminalCardData {
  article: string;
  article_bag: string;
  gender: string;
  type: string;
  maker: string;
  series: string;
  product: string;
  step: string;
  strip: string;
  l_plus: string;
  l_minus: string;
  applicator: string;
  crimp_height: string;
  ins_crimp_height: string;
  pull_force_min: string;
  pull_force_max: string;
  awg_from: string;
  awg_to: string;
  mm2_from: string;
  mm2_to: string;
  ins_od_from: string;
  ins_od_to: string;
  material: string;
  max_current: string;
  datasheet: string;
}

/** A «Терминалы» record as GET /api/references/tech-operations?tab=ter returns it: with its contact card. */
export type TerminalRow = TechOperationRecord & { card?: TerminalCardData };

const GENDER: Record<string, string> = { Male: "Штырь (папа)", Female: "Гнездо (мама)" };

// «-» / «—» in a sheet cell is an empty cell (null-spec), not a value.
const trim = (value?: string) => {
  const text = String(value ?? "").trim();
  return /^[-–—]$/.test(text) ? "" : text;
};

// Protocol v5.2: datasheets from the maker's site only. Mirror rows that link a distributor are marked, not hidden.
const DISTRIBUTORS = /(^|\.)(digikey|mouser|rs-online|farnell|lcsc|chipdip|promelec|electro-mpo|definum|akompany|compel|platan|elitan|terraelectronica)\./i;

export function distributorLink(url: string): boolean {
  try { return DISTRIBUTORS.test(new URL(url).hostname); } catch { return false; }
}

/** «28–26», «до 1,7», «от 0,8»: a range where only one side is in the spec keeps that side. */
export function rangeText(from?: string, to?: string): string {
  const low = trim(from);
  const high = trim(to);
  if (low && high) return low === high ? low : `${low}–${high}`;
  if (high) return `до ${high}`;
  return low ? `от ${low}` : "";
}

/** «24 AWG: 0,78–0,88; 22 AWG: 0,80–0,90» → one AWG per line, as the table's crimp column reads. */
export function profileLines(text: string): string {
  return trim(text).split(/\s*;\s*(?=\d+(?:\s*[-–]\s*\d+)?\s*AWG)/i).join("\n");
}

function pullText(card: TerminalCardData): string {
  const low = trim(card.pull_force_min);
  const high = trim(card.pull_force_max);
  // A per-AWG profile («28 AWG: 19,6») is shown one AWG per line; a plain pair as a range.
  return high && low && high !== low && !low.includes(":") ? `${low}–${high}` : profileLines(low || high);
}

function Datasheet({ url }: { url: string }) {
  let host = url;
  try { host = new URL(url).hostname.replace(/^www\./, ""); } catch { /* shown as written */ }
  return <>
    <a href={url} target="_blank" rel="noreferrer" title={url}>{host} ↗</a>
    {distributorLink(url) && <> <span className="order-chip tone-warn" title="Протокол: даташит — с сайта производителя">сайт дистрибьютора</span></>}
  </>;
}

type Line = [title: string, value: string, options?: { wide?: boolean; link?: boolean; hint?: string }];

function Fields({ lines }: { lines: Line[] }) {
  return <dl className="op-fields">{lines.map(([title, value, options]) => <div key={title}>
    <dt title={options?.hint}>{title}</dt>
    <dd className={options?.link ? undefined : "pre-line tnum"}>{value
      ? options?.link && /^https?:\/\//.test(value) ? <Datasheet url={value} /> : value
      : <span className="muted" title="Нет в спецификации производителя">—</span>}</dd>
  </div>)}</dl>;
}

export function terminalCardLines(card: TerminalCardData): { contact: Line[]; crimp: Line[] } {
  const awg = rangeText(card.awg_from, card.awg_to);
  const mm2 = rangeText(card.mm2_from, card.mm2_to);
  const lengths = [trim(card.l_plus) && `L+ ${trim(card.l_plus)}`, trim(card.l_minus) && `L− ${trim(card.l_minus)}`].filter(Boolean).join(" · ");
  return {
    contact: [
      ["Пол", GENDER[card.gender] ?? ""],
      ["Артикул REEL (катушка)", trim(card.article)],
      ["Артикул BAG (россыпь)", trim(card.article_bag)],
      ["Шаг, мм", trim(card.step)],
      ["Материал / покрытие", trim(card.material)],
      ["Макс. ток, А", trim(card.max_current)],
      ["Даташит", trim(card.datasheet), { link: true }],
    ],
    crimp: [
      ["Провод", [awg && `${awg} AWG`, mm2 && `${mm2} мм²`].filter(Boolean).join(" · ")],
      ["Высота обжима проводника, мм", profileLines(card.crimp_height)],
      ["Высота обжима изоляции, мм", profileLines(card.ins_crimp_height)],
      ["Диаметр изоляции, мм", rangeText(card.ins_od_from, card.ins_od_to)],
      ["Усилие обрыва, Н", pullText(card)],
      ["Длина зачистки, мм", trim(card.strip)],
      ["L+ / L−, мм", lengths, { hint: `${TER_L_PLUS_HINT}
${TER_L_MINUS_HINT}` }],
      ["Аппликатор", trim(card.applicator)],
    ],
  };
}

export function TerminalCard({ record, canEdit, onEdit, onClose }: {
  record: TerminalRow;
  canEdit: boolean;
  onEdit: (record: TechOperationRecord) => void;
  onClose: () => void;
}) {
  const card = record.card;
  const subtitle = card ? [card.maker, card.series, card.product].map(trim).filter(Boolean)
    .filter((part, index, parts) => parts.indexOf(part) === index).join(" · ") : "";
  const lines = card ? terminalCardLines(card) : null;
  return <PageWindow label="Карточка контакта" width={640} dismissable close={onClose}>
    <article className="panel eq-card">
      <header className="eq-head">
        <div className="eq-title"><b className="tnum">{trim(card?.article) || record.display_text || "—"}</b>{subtitle && <span className="muted">{subtitle}</span>}</div>
        <div className="eq-head-act">
          {canEdit && <button type="button" className="secondary small" onClick={() => onEdit(record)}>Править запись</button>}
          <button type="button" className="eq-close" aria-label="Закрыть карточку" title="Закрыть (Esc)" onClick={onClose}>×</button>
        </div>
      </header>
      {lines
        ? <>
          <div className="tr-block"><h4>Контакт</h4><Fields lines={lines.contact} /></div>
          <div className="tr-block"><h4>Обжим</h4><Fields lines={lines.crimp} /></div>
        </>
        : <div className="empty">Карточка не пришла с сервера — обновите страницу.</div>}
    </article>
  </PageWindow>;
}
