import type { ReactNode } from "react";
import type { KitsetTile } from "./api";
import { formatDateTime } from "./format";
import { ProjectStatus } from "./ProjectStatus";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";

/** Карточка проекта как в SUPPLYMAP «Проекты» (статус, блокер, ТК, проверка, набор, заказчик, сборки · позиции).
 *  Общая для списка проектов и экрана мастера. Статусы — общим `StatusChip` (аудит дизайна 10.10, ПР-4). */

/** Сколько КБ с выданной ТК и сколько из них без отметки проверки — те же числа, что в строке «Проверка ТК». */
export function tcProgress(item: KitsetTile) {
  const kbs = item.kb_stages;
  const tc = kbs.filter((kb) => kb.tc).length;
  const unreviewed = kbs.filter((kb) => kb.tc && !(kb.review?.value && kb.review.value !== "не проверена")).length;
  return { total: kbs.length, tc, unreviewed };
}

type Fact = { text: string; tone: Tone; title: string };

/** Факты плитки про КБ: техкарты и набор — «из скольких КБ», чтобы число читалось без подсказки (ПР-11). */
export function tileFacts(item: KitsetTile, statuses: string[]): Fact[] {
  const kbs = item.kb_stages;
  const { total, tc } = tcProgress(item);
  const kit = kbs.filter((kb) => kb.kit).length;
  const index = statuses.indexOf(item.status);
  const shipped = statuses.indexOf("Отгружено");
  const late = index >= statuses.indexOf("В работе") && index < shipped;
  // «Отменён» стоит после «Отгружено»: у закрытого проекта факты не нужны.
  if (!total || index > shipped) return [];
  const facts: Fact[] = [];
  facts.push(tc === total ? { text: "ТК на все КБ", tone: "success", title: "Техкарты выданы на все КБ" }
    : tc ? { text: `ТК ${tc} из ${total} КБ`, tone: "warning", title: "Техкарты выданы не на все КБ" }
      : { text: "ТК нет", tone: "neutral", title: "Техкарт ещё нет" });
  if (kit === total) facts.push({ text: "набрано на все КБ", tone: "success", title: "Комплектация набрана на все КБ" });
  else if (kit) facts.push({ text: `набрано ${kit} из ${total} КБ`, tone: "warning", title: "Комплектация набрана не на все КБ" });
  else if (late) facts.push({ text: "не набрано", tone: "neutral", title: "Заявки на комплектацию не набраны" });
  return facts;
}

/** Проверка выданных ТК по КБ: «проверена: КБ1 · не проверена: КБ2 · с погрешностями: КБ3» — видно, о каких КБ речь. */
export function reviewLine(item: KitsetTile): Array<{ label: string; kbs: string[]; tone: Tone }> {
  const groups: Array<{ label: string; kbs: string[]; tone: Tone }> = [
    { label: "проверена", kbs: [], tone: "success" },
    { label: "не проверена", kbs: [], tone: "warning" },
    { label: "с погрешностями", kbs: [], tone: "danger" },
  ];
  for (const kb of item.kb_stages) {
    if (!kb.tc) continue;
    const value = kb.review?.value || "не проверена";
    (groups.find((group) => group.label === value) ?? groups[1]).kbs.push(kb.index);
  }
  return groups.filter((group) => group.kbs.length);
}

// `extra` — строка экрана мастера (ДЛ, операции без исполнителя, к запуску).
export function KitTile({ item, statuses, canEdit, open, extra, statusChanged, lists }: {
  item: KitsetTile; statuses: string[]; canEdit: boolean; open: () => void; extra?: ReactNode;
  /** Есть — статус меняется прямо на плитке в один клик (меню); нет — только чип. */
  statusChanged?: () => void;
  lists?: { stages?: string[]; terminals?: string[]; blockers?: string[] };
}) {
  const kbs = item.kb_stages;
  const mixed = kbs.some((kb) => kb.status !== kbs[0].status);
  const reviews = reviewLine(item);
  return <div className="tile kit-tile" role="button" tabIndex={0} onClick={open} onKeyDown={(event) => { if (event.key === "Enter") open(); }}>
    <div className="tile-body">
      <div className="kit-tile-head">
        <strong className="t-h">{item.project}</strong>
      </div>
      <div className="kit-chips">
        {statusChanged
          ? <ProjectStatus project={item.project} status={item.status} blocker={item.blocker} canEdit={canEdit} {...lists} onChanged={statusChanged} />
          : <><StatusChip status={item.status} />{item.blocker && <StatusChip status={item.blocker} title="Активный блокер из журнала проекта" />}</>}
        {tileFacts(item, statuses).map((fact) => <StatusChip key={fact.text} status={fact.text} tone={fact.tone} title={fact.title} />)}
      </div>
      {mixed && <div className="t-sub">{kbs.map((kb) => `${kb.index} — ${kb.status}`).join(" · ")}</div>}
      {reviews.length > 0 && <div className="t-in kit-review">Проверка ТК: {reviews.map((group, position) =>
        <span key={group.label} className={`kit-review-${group.tone}`}>{position > 0 && " · "}{group.label} — {group.kbs.join(", ")}</span>)}</div>}
      <div className="t-sub">{item.customer || "—"}</div>
      <div className="t-in">сборок {item.assemblies} · позиций {item.lines}</div>
      <div className="t-sub">обновлено {formatDateTime(item.updated_at)}</div>
      {extra}
    </div>
  </div>;
}
