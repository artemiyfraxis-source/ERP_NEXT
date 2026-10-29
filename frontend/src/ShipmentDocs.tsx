import { useState } from "react";
import { PageWindow } from "./PageWindow";
import type { DispatchShipment } from "./productionIntakeApi";
import { ACTIVE, Banners, BoxesEditor, needsUpdFile, useMutate, useShipmentPrint, withoutUpd, type CardRights } from "./ShipmentCard";
import { updLabel } from "./ShipmentData";

/* «Документы» отправки на «Приёмке с производства» (владелец 29.10.2026: «сначала формирование, потом кнопка открытия УПД,
   DN, и в этом окне уже печать»). В строке КБ — только «Сформировать отгрузочные» или одна кнопка «Документы…»; PL, DN,
   этикетки и УПД печатаются в окне. */

export interface UpdState { text: string; tone: "ok" | "warn" | "danger" | "muted"; href: string }

/** УПД отправки с типом: «УПД · Бумага № 485» (файл — ссылка), «УПД · Бумага — нет файла» (без файла бумагу не отгрузить),
 *  «УПД — тип не выбран», «Без УПД». */
export function updState(shipment: DispatchShipment): UpdState {
  if (withoutUpd(shipment)) return { text: "Без УПД", tone: "muted", href: "" };
  const type = updLabel(shipment.upd_type);
  if (!type) return { text: "УПД — тип не выбран", tone: "warn", href: "" };
  const base = `УПД · ${type}${shipment.upd_number ? ` № ${shipment.upd_number}` : ""}`;
  if (shipment.upd_url) return { text: base, tone: "ok", href: shipment.upd_url };
  if (needsUpdFile(shipment)) return { text: `${base} — нет файла`, tone: "danger", href: "" };
  return { text: base, tone: "ok", href: "" };
}

/** Короткая подпись кнопки строки: «Документы: DN, УПД · Бумага». */
export function docsLabel(shipment: DispatchShipment) {
  const type = updLabel(shipment.upd_type);
  const upd = withoutUpd(shipment) ? "без УПД" : type ? `УПД · ${type}` : "УПД не выбран";
  return `Документы: DN, ${upd}`;
}

export function ShipmentDocsWindow({ shipment, rights, reload, close }: {
  shipment: DispatchShipment; rights: CardRights; reload: () => Promise<void> | void; close: () => void;
}) {
  const { busy, error, notice, mutate } = useMutate(reload);
  const { print, printLabels, printError, canPrintDocs, dnGaps } = useShipmentPrint(shipment);
  const [layout, setLayout] = useState(false);
  const upd = updState(shipment);
  const active = ACTIVE.includes(shipment.status);
  return <PageWindow label={`Документы отправки ${shipment.number}`} close={close} width={640}>
    <section className="panel intake-window intake-docs-window">
      <header className="page-head"><h2>Документы отправки · {shipment.number}</h2><button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      <Banners error={error || printError} notice={notice} />
      <ul className="docs-list">
        <li><strong>PL</strong><span>{shipment.pl_number}</span>
          {canPrintDocs && <button type="button" className="secondary small" aria-label="Печать PL" onClick={() => void print("pl")}>Печать</button>}</li>
        <li><strong>DN</strong><span>{shipment.dn_number}</span>
          {canPrintDocs && <button type="button" className="secondary small" aria-label="Печать DN" disabled={dnGaps.length > 0}
            title={dnGaps.length ? `DN печатается, когда внесены данные. Не хватает: ${dnGaps.join(", ")}` : undefined} onClick={() => void print("dn")}>Печать</button>}
          {dnGaps.length > 0 && <span className="tone-warn">не хватает: {dnGaps.join(", ")}</span>}</li>
        <li><strong>УПД</strong><span className={upd.tone === "ok" ? undefined : `tone-${upd.tone === "muted" ? "neutral" : upd.tone}`}>{upd.text}</span>
          {upd.href && <a className="secondary small docs-link" href={upd.href} target="_blank" rel="noreferrer">Открыть / печать</a>}</li>
        <li><strong>Этикетки</strong><span className="muted">мест {shipment.places}</span>
          <button type="button" className="secondary small" aria-label="Печать этикеток" onClick={() => void printLabels()}>Печать</button></li>
      </ul>
      {rights.canEdit && active && !layout && <div className="intake-window-actions">
        <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => setLayout(true)}>Перевыпустить</button></div>}
      {layout && <BoxesEditor shipment={shipment} busy={busy} mutate={mutate} onDone={() => setLayout(false)} />}
    </section>
  </PageWindow>;
}
