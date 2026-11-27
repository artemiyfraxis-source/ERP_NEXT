import { useEffect, useRef, useState, type ReactNode } from "react";
import { QualityChip } from "./GrnFields";
import { IntakeShipmentInfo } from "./IntakeShipmentInfo";
import { PageWindow } from "./PageWindow";
import { StatusChip } from "./StatusChip";
import { needsTrack } from "./carriers";
import { formatDay, formatNumber, NBSP } from "./format";
import { updGapAction, type ActionId, type BoardCard, type CardAction } from "./intakeBoard";
import { handedOf, leftOf } from "./IntakeWindow";
import { num, packable, permittedPack } from "./intakeStages";
import type { ActShare } from "./dispatchPositionRows";
import type { DispatchShipment, IntakeAct, IntakeRow } from "./productionIntakeApi";
import { shipmentGaps } from "./intakeBoard";
import { shipmentTitle } from "./shipmentTitle";
import { leaveUnsaved } from "./EditSection";

/* Окно карточки доски (владелец 27.11: «как сейчас — удобно»): сверху — что это, главная кнопка и «почему нельзя дальше»,
   у отправки — шаги документов по порядку (PL → DN → УПД → трек), ниже — строки по КБ и правка данных на месте
   (`IntakeShipmentInfo`: карандаш у поля или «Редактировать» блока). */

const dash = <span className="muted">—</span>;
const UPD_WORDS = ["тип УПД", "№ УПД", "файл УПД", "разрешение на отгрузку без УПД"];
/** Что в окне открыть сразу: поле, у которого стоит карандаш. */
export type FocusField = "upd" | "upd_number" | "upd_file" | "track";
export const FOCUS_OF: Partial<Record<ActionId, FocusField>> = { "enter-upd": "upd", "enter-upd-number": "upd_number", "enter-upd-file": "upd_file", "enter-track": "track" };
const FIELD_LABEL: Record<Exclude<FocusField, "upd_file">, string> = { upd: "УПД", upd_number: "№ УПД", track: "Трек" };

export interface WindowRights { canEdit: boolean; canHistory: boolean; canWarehouse: boolean; canRollback: boolean }

function Step({ index, title, done, value, button }: { index: number; title: string; done: boolean; value?: ReactNode; button?: ReactNode }) {
  return <li className={`board-step${done ? " is-done" : ""}`} data-step={title}>
    <span className="board-step-title">{index} {title}</span>
    <span className="board-step-value">{done ? <><span className="tone-ok" aria-hidden="true">✓</span> {value}</> : button ?? <span className="muted">нет</span>}</span>
  </li>;
}

export function IntakeCardWindow({ card, rights, focus, busy, docLink, shares, onAction, onShipment, onHistory, onConflict, onDelivery, onRollback, onPrintLabel, close }: {
  card: BoardCard; rights: WindowRights; focus: FocusField | null; busy: boolean;
  docLink: (shipment: DispatchShipment, document: "pl" | "dn") => ReactNode;
  /** Остаток КБ на складе по актам приёмки. */
  shares: (kb: IntakeRow) => ActShare[];
  onAction: (card: BoardCard, action: CardAction) => void;
  onShipment: (next: DispatchShipment) => void;
  onHistory: (shipment: DispatchShipment) => void;
  onConflict: () => Promise<void> | void;
  onDelivery: (card: BoardCard) => void;
  onRollback: (kb: IntakeRow, act: IntakeAct, onStock: number) => void;
  onPrintLabel: (grn: string) => void;
  close: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const shipment = card.shipment;
  const [positionsOpen, setPositionsOpen] = useState(true);
  const title = shipment ? shipmentTitle(shipment) : `${card.project}${card.customer ? ` · ${card.customer}` : ""}`;
  const pencilOf = (label: string) => box.current?.querySelector<HTMLElement>(`[data-label="${label}"] .inline-field-pencil`);
  const focusField = (field: FocusField) => {
    if (field === "upd_file") box.current?.querySelector<HTMLElement>('button[aria-label^="Загрузить файл УПД"], button[aria-label^="Заменить файл УПД"]')?.click();
    else pencilOf(FIELD_LABEL[field])?.click();
  };
  useEffect(() => { if (focus) focusField(focus); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const guardedClose = () => { if (leaveUnsaved(shipment?.id)) close(); };

  const head = <header className="page-head board-window-head">
    <div><h2>{title}</h2><p className="muted board-window-sub">{card.deliveryLine}{card.sequence ? ` · ${card.sequence}` : ""}</p></div>
    <div className="board-window-actions">
      {shipment && <StatusChip status={shipment.status} />}
      {card.action && <button type="button" className={card.action.disabled ? "secondary small" : "primary small"} disabled={card.action.disabled || busy}
        title={card.action.note} onClick={() => { const field = FOCUS_OF[card.action!.id]; if (field && shipment) focusField(field); else onAction(card, card.action!); }}>{card.action.label}</button>}
      <button type="button" className="secondary small" onClick={guardedClose}>Закрыть</button>
    </div>
  </header>;
  const status = card.blocker
    ? <div className="board-window-blocker" role="note"><span className="tone-warn">⚠ {card.blocker.text}</span><span className="muted"> — {card.blocker.who}</span></div>
    : card.okText ? <div className="board-window-blocker tone-ok">✓ {card.okText}</div> : null;

  if (!shipment) {
    const production = card.column === "production";
    return <PageWindow label={`Карточка ${title}`} close={guardedClose} width={1280}><div ref={box}><section className="panel intake-window board-window">
      {head}{status}
      <div className="board-table-wrap"><table className="board-table">
        <thead><tr><th>КБ</th><th>Наименование</th>{production
          ? <><th className="num">Заказано</th><th className="num">Запущено</th><th className="num">Сдано цехом</th><th className="num">Принято</th><th className="num">Остаток</th><th>Задания</th></>
          : <><th>ГРН</th><th>Качество</th><th>Принято</th><th className="num">На складе</th><th className="num">Можно упаковать</th><th>Разрешение</th></>}</tr></thead>
        <tbody>{card.kbs.map((kb) => production
          ? <tr key={kb.item_index}><td>{kb.item_index}</td><td>{kb.name}</td><td className="num">{formatNumber(num(kb.needed))}</td><td className="num">{num(kb.launched) > 0 ? formatNumber(num(kb.launched)) : dash}</td>
            <td className="num">{handedOf(kb) > 0 ? formatNumber(handedOf(kb)) : dash}</td><td className="num">{num(kb.accepted) > 0 ? formatNumber(num(kb.accepted)) : dash}</td>
            <td className="num">{leftOf(kb) > 0 ? formatNumber(leftOf(kb)) : dash}</td>
            <td className="muted" title={kb.tasks.map((task) => `${task.number} · сдано ${task.delivered} из ${task.quantity}`).join("\n")}>{kb.tasks.map((task) => task.number).join(", ") || "—"}</td></tr>
          : (shares(kb).length ? shares(kb) : [null]).map((share, index) => <tr key={`${kb.item_index}:${share?.grn ?? index}`}>
            <td>{kb.item_index}</td><td>{kb.name}</td>
            <td>{share?.act && rights.canRollback ? <button type="button" className="link-button" title="Карточка акта приёмки: откат" aria-label={`Акт приёмки ${share.act.grn}`}
              onClick={() => onRollback(kb, share.act!, share.quantity)}>{share.act.grn}</button> : share?.grn || dash}
              {share && rights.canWarehouse && share.grn && <> <button type="button" className="link-button" aria-label={`Печать этикетки ${share.grn}`} onClick={() => onPrintLabel(share.grn)}>Печать</button></>}</td>
            <td>{share?.act?.grn_contract_version === 1 || share?.act?.quality_state ? <QualityChip passport={share.act} /> : dash}</td>
            <td>{share?.act ? formatDay(share.act.received_at) : dash}</td>
            <td className="num">{share ? `${formatNumber(share.quantity)}${NBSP}${kb.unit}` : `${formatNumber(packable(kb))}${NBSP}${kb.unit}`}</td>
            <td className="num">{index === 0 ? formatNumber(permittedPack(kb)) : ""}</td>
            <td>{index === 0 ? (permittedPack(kb) > 0 || (kb as IntakeRow & { pack_auto_allowed?: boolean }).pack_auto_allowed
              ? <span className="tone-ok">{(kb as IntakeRow & { pack_auto_allowed?: boolean }).pack_auto_allowed ? "не нужно: весь заказ принят" : "разрешено"}</span> : <span className="tone-warn">нужно разрешение</span>) : ""}</td></tr>))}</tbody>
      </table></div>
      {rights.canEdit && <p className="board-window-links"><button type="button" className="link-button" onClick={() => onDelivery(card)}>Доставка КБ…</button></p>}
    </section></div></PageWindow>;
  }

  const gaps = shipmentGaps(shipment);
  const picked = shipment.lines.filter((line) => num(line.picked) > 0);
  const active = ["Собрана", "Упакована"].includes(shipment.status) && rights.canEdit;
  const updMissing = Boolean(updGapAction(shipment)) || gaps.some((gap) => UPD_WORDS.includes(gap));
  const updAction = updGapAction(shipment);
  const items = [...new Map(picked.map((line) => [line.item_index, line])).values()];
  const track = shipment.track_number.trim();
  const trackNeeded = needsTrack(shipment);
  return <PageWindow label={`Карточка ${title}`} close={guardedClose} width={1280}><div ref={box}><section className="panel intake-window board-window">
    {head}{status}
    <ol className="board-steps" aria-label="Документы по порядку">
      <Step index={1} title="PL" done={Boolean(shipment.pl_number)} value={docLink(shipment, "pl")}
        button={active ? <button type="button" className="secondary small" disabled={busy} onClick={() => onAction(card, { id: "make-pl", label: "Сформировать PL" })}>Сформировать PL</button> : undefined} />
      <Step index={2} title="DN" done={Boolean(shipment.dn_number)} value={docLink(shipment, "dn")}
        button={active && shipment.pl_number ? <button type="button" className="secondary small" disabled={busy} onClick={() => onAction(card, { id: "make-dn", label: "Сформировать DN" })}>Сформировать DN</button>
          : <span className="muted">после PL</span>} />
      <Step index={3} title="УПД" done={!updMissing} value={shipment.upd_type === "БЕЗ УПД" ? "без УПД, разрешено" : [shipment.upd_type, shipment.upd_number && `№ ${shipment.upd_number}`].filter(Boolean).join(" ") || "внесён"}
        button={rights.canEdit ? <button type="button" className="secondary small" onClick={() => focusField(updAction?.id === "enter-upd-number" ? "upd_number" : updAction?.id === "enter-upd-file" ? "upd_file" : "upd")}>{updAction?.label ?? "Внести УПД…"}</button> : undefined} />
      <Step index={4} title="Трек" done={!trackNeeded || Boolean(track)} value={trackNeeded ? track : "не нужен"}
        button={rights.canEdit && (shipment.ready_for_track ?? false) ? <button type="button" className="secondary small" onClick={() => focusField("track")}>Вписать трек…</button>
          : <span className="muted">после УПД и ТК</span>} />
    </ol>
    <div className="board-positions">
      <button type="button" className="intake-fold" aria-expanded={positionsOpen} onClick={() => setPositionsOpen(!positionsOpen)}>
        <span aria-hidden="true">{positionsOpen ? "▾" : "▸"}</span> Позиции ({picked.length})</button>
      {positionsOpen && <div className="board-table-wrap"><table className="board-table">
        <thead><tr><th>КБ</th><th>Наименование</th><th>ГРН</th><th className="num">Количество</th></tr></thead>
        <tbody>{picked.map((line) => <tr key={line.id}><td>{card.project.includes("+") ? `${line.project} ` : ""}{line.item_index}</td><td>{line.name}</td>
          <td>{(line.grns?.length ? line.grns : [line.grn]).filter(Boolean).join(", ") || dash}</td><td className="num">{formatNumber(num(line.picked))}{NBSP}{line.unit}</td></tr>)}</tbody>
      </table></div>}
    </div>
    <IntakeShipmentInfo shipment={shipment} gaps={gaps} rights={{ canEdit: rights.canEdit, canHistory: rights.canHistory }}
      pl={docLink(shipment, "pl")} dn={docLink(shipment, "dn")} onShipment={onShipment} onHistory={() => onHistory(shipment)} onConflict={onConflict}
      deliveryButton={rights.canEdit && items.length ? <button type="button" className="link-button" aria-label={`Доставка КБ ${title}`} onClick={() => onDelivery(card)}>Доставка КБ…</button> : null} />
  </section></div></PageWindow>;
}
