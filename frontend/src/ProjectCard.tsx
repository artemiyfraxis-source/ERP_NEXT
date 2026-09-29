import type { ReactNode } from "react";
import type { KitsetTile } from "./api";

/** Карточка проекта как в SUPPLYMAP «Проекты» (статус, блокер, ТК, проверка, набор, заказчик, сборки · позиции).
 *  Общая для списка проектов и экрана мастера. */
export function toneOf(status: string, statuses: string[]) {
  const index = statuses.indexOf(status);
  const done = statuses.indexOf("Отгружено");
  if (!status || index <= 0) return "neutral";
  if (index > done) return "danger";
  if (index === done) return "done";
  if (status === "КБ на складе") return "stock";
  if (status === "В работе") return "work";
  return "prep";
}

// `extra` — строка экрана мастера (ДЛ, операции без исполнителя, к запуску); без `remove` корзины нет.
export function KitTile({ item, statuses, canEdit, open, remove, extra }: {
  item: KitsetTile; statuses: string[]; canEdit: boolean; open: () => void; remove?: () => void; extra?: ReactNode;
}) {
  const kbs = item.kb_stages;
  const mixed = kbs.some((kb) => kb.status !== kbs[0].status);
  const reviews = kbs.map((kb) => kb.review?.value);
  const ok = reviews.filter((value) => value === "проверена").length;
  const bad = reviews.filter((value) => value === "с погрешностями").length;
  const total = kbs.length;
  const tc = kbs.filter((kb) => kb.tc).length;
  const kit = kbs.filter((kb) => kb.kit).length;
  const unreviewed = kbs.filter((kb) => kb.tc && !(kb.review?.value && kb.review.value !== "не проверена")).length;
  const index = statuses.indexOf(item.status);
  const late = index >= statuses.indexOf("В работе") && index < statuses.indexOf("Отгружено");
  const facts: Array<[string, string, string]> = [];
  if (item.blocker) facts.push([`⛔ ${item.blocker}`, "danger", "Активный блокер из журнала проекта"]);
  // Отказ/Слился/Отменён стоят после «Отгружено»: у закрытого проекта факты не нужны.
  if (total && index <= statuses.indexOf("Отгружено")) {
    facts.push(tc === total ? ["ТК ✓", "ok", "Техкарты выданы на все КБ"] : tc ? [`ТК ${tc}/${total}`, "warn", "Техкарты выданы не на все КБ"] : ["ТК нет", "neutral", "Техкарт ещё нет"]);
    if (tc && unreviewed) facts.push([`не проверено ${unreviewed}`, "warn", "Выданные ТК без отметки проверки"]);
    if (kit === total) facts.push(["набрано ✓", "ok", "Комплектация набрана на все КБ"]);
    else if (kit) facts.push([`набрано ${kit}/${total}`, "warn", "Комплектация набрана не на все КБ"]);
    else if (late) facts.push(["не набрано", "neutral", "Заявки на комплектацию не набраны"]);
  }
  return <div className="tile kit-tile" role="button" tabIndex={0} onClick={open} onKeyDown={(event) => { if (event.key === "Enter") open(); }}>
    <div className="tile-body">
      <div className="kit-tile-head">
        <strong className="t-h">{item.project}</strong>
        {canEdit && remove && <button className="link-button danger-link" title="Удалить карточку проекта" aria-label={`Удалить ${item.project}`} onClick={(event) => { event.stopPropagation(); remove(); }}>🗑</button>}
      </div>
      <div className="kit-chips">
        <span className={`kit-status tone-${toneOf(item.status, statuses)}`}>{item.status || "—"}</span>
        {facts.map(([text, tone, title]) => <span key={text} className={`kit-fact tone-${tone}`} title={title}>{text}</span>)}
      </div>
      {mixed && <div className="t-sub">{kbs.map((kb) => `${kb.index} — ${kb.status}`).join(" · ")}</div>}
      {(ok > 0 || bad > 0) && <div className="t-in">
        {ok > 0 && <span className="kit-ok">✓ ТК проверено {ok} </span>}
        {bad > 0 && <span className="kit-bad">⚠ с погрешностями {bad}</span>}
      </div>}
      <div className="t-sub">{item.customer || "—"}</div>
      <div className="t-in">сборок {item.assemblies} · позиций {item.lines}</div>
      <div className="t-sub">обновлено {new Date(item.updated_at).toLocaleString("ru-RU")}</div>
      {extra}
    </div>
  </div>;
}
