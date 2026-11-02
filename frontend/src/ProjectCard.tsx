import { useId, type ReactNode } from "react";
import type { KitsetTile } from "./api";
import { formatDateTime } from "./format";
import { ProjectMarks, ProjectStatus } from "./ProjectStatus";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";

/** Сколько КБ с выданной ТК и сколько из них без отметки проверки — те же числа, что в строке «Проверка ТК». */
export function tcProgress(item: KitsetTile) {
  const kbs = item.kb_stages;
  const tc = kbs.filter((kb) => kb.tc).length;
  const unreviewed = kbs.filter((kb) => kb.tc && !(kb.review?.value && kb.review.value !== "не проверена")).length;
  return { total: kbs.length, tc, unreviewed };
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

// `extra` — строка экрана мастера (ДЛ, операции без исполнителя, КБ без задания).
export function KitTile({ item, statuses, canEdit, open, extra, statusChanged, lists }: {
  item: KitsetTile; statuses: string[]; canEdit: boolean; open: () => void; extra?: ReactNode;
  /** Есть — статус меняется прямо на плитке в один клик (меню); нет — только чип. */
  statusChanged?: () => void;
  lists?: { stages?: string[]; terminals?: string[]; blockers?: string[] };
}) {
  const kbs = item.kb_stages;
  const reviews = reviewLine(item);
  const descriptionId = useId();
  const shipped = statuses.indexOf("Отгружено");
  const closed = shipped >= 0 && statuses.indexOf(item.status) > shipped;
  const noTc = item.tc_ready ? [] : kbs.filter((kb) => !kb.tc).map((kb) => kb.index);
  const notPicked = item.kit_picked ? [] : kbs.filter((kb) => !kb.kit).map((kb) => kb.index);
  const unreviewed = reviews.find((group) => group.label === "не проверена")?.kbs ?? [];
  const defects = reviews.find((group) => group.label === "с погрешностями")?.kbs ?? [];
  const lines = closed ? [] : [
    [
      { text: "не выдана", label: "ТК не выдана", kbs: noTc, tone: "warning" },
      { text: "не проверена", label: "ТК не проверена", kbs: unreviewed, tone: "warning" },
    ],
    [
      { text: "С погрешностями", label: "ТК с погрешностями", kbs: defects, tone: "danger" },
      { text: "КБ не набрана", label: "КБ не набрана", kbs: notPicked, tone: "warning" },
    ],
  ].map((line) => line.filter((fact) => fact.kbs.length));
  const hasProblems = lines.some((line) => line.length);
  const details = [
    `Заказчик: ${item.customer || "—"}`,
    `Статус проекта: ${item.status || "—"}`,
    ...(item.blocker ? [`Блокер: ${item.blocker}`] : []),
    ...(item.tc_manual ? [`ТК: ${item.tc_manual} — вручную`] : []),
    ...(item.kit_manual ? [`КБ: ${item.kit_manual} — вручную`] : []),
    `КБ: ${kbs.map((kb) => `${kb.index} — ${kb.status}`).join(" · ") || "—"}`,
    ...reviews.map((group) => `Проверка ТК: ${group.label} — ${group.kbs.join(", ")}`),
    ...lines.flat().map((fact) => `${fact.label}: ${fact.kbs.length} КБ — ${fact.kbs.join(", ")}`),
    `сборок ${item.assemblies} · позиций ${item.lines}`,
    `обновлено ${formatDateTime(item.updated_at) || "—"}`,
  ].join("\n");
  return <div className="tile kit-tile" role="button" tabIndex={0} aria-label={`Открыть проект ${item.project}`}
    aria-describedby={descriptionId} title={details} onClick={open} onKeyDown={(event) => {
      if (event.target === event.currentTarget && !event.repeat && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault(); open();
      }
    }}>
    <div className="tile-body">
      <div className="kit-tile-head">
        <strong className="t-h">{item.project}</strong>
      </div>
      <div className="kit-tile-customer" title={item.customer || undefined}>{item.customer || "—"}</div>
      <div className="kit-chips">
        {statusChanged
          ? <ProjectStatus project={item.project} status={item.status} blocker={item.blocker} canEdit={canEdit}
            tcReady={item.tc_ready} kitPicked={item.kit_picked} tcManual={item.tc_manual} kitManual={item.kit_manual} {...lists} onChanged={statusChanged} />
          : <><StatusChip status={item.status} /><ProjectMarks tcReady={item.tc_ready} kitPicked={item.kit_picked} tcManual={item.tc_manual} kitManual={item.kit_manual} />{item.blocker && <StatusChip status={item.blocker} title="Активный блокер из журнала проекта" />}</>}
      </div>
      <div className="kit-tile-problems">
        {hasProblems && lines.map((line, row) => <div className="kit-tile-problem-line" key={row}>
          {row === 0 && line.length > 0 && "ТК: "}
          {line.map((fact, position) => <span key={fact.label} data-tone={fact.tone}
            title={`${fact.label}: ${fact.kbs.length} КБ — ${fact.kbs.join(", ")}`}>
            {position > 0 && " · "}{fact.text} <b>{fact.kbs.length}</b>
          </span>)}
        </div>)}
      </div>
      <div className="kit-tile-details" id={descriptionId}>{details}</div>
      {extra}
    </div>
  </div>;
}
