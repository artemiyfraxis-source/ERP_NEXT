import { useState } from "react";
import { PageWindow } from "./PageWindow";
import { intakeApi, type DispatchShipment } from "./productionIntakeApi";
import { Banners, readyForTrack, ShipStatus, useMutate } from "./ShipmentCard";
import { Readiness } from "./ShipmentData";

/* «Трек…» (владелец 29.10.2026): трек-номер ТК кладовщик вписывает, когда у упакованной отправки есть УПД и ТК —
   раньше сервер его не примет. Одна строка на отправку КБ. */

export function TrackWindow({ title, shipments, reload, close }: {
  title: string; shipments: DispatchShipment[]; reload: () => Promise<void> | void; close: () => void;
}) {
  const { busy, error, notice, mutate } = useMutate(reload);
  const [tracks, setTracks] = useState<Record<string, string>>(() => Object.fromEntries(shipments.map((shipment) => [shipment.id, shipment.track_number])));
  return <PageWindow label={`Трек ${title}`} close={close} width={980}>
    <section className="panel intake-window intake-track">
      <header className="page-head"><h2>Трек-номер · {title}</h2><button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      <Banners error={error} notice={notice} />
      {shipments.length === 0 && <div className="empty">Упакованных отправок с доставкой ТК нет.</div>}
      {shipments.map((shipment) => {
        const ready = readyForTrack(shipment) || Boolean(shipment.track_number);
        const gaps = shipment.missing_ship.filter((gap) => gap !== "трек-номер");
        const value = tracks[shipment.id] ?? "";
        return <div key={shipment.id} className="intake-track-row" role="group" aria-label={`Трек ${shipment.number}`}>
          <div className="intake-track-head"><strong>{shipment.number}</strong><ShipStatus status={shipment.status} />
            {shipment.carrier && <span className="muted">{shipment.carrier}</span>}
            <Readiness source={shipment} gaps={shipment.missing_ship} withTrack /></div>
          <div className="intake-window-actions">
            {!ready && <span className="tone-warn">Трек вносится, когда есть УПД и ТК{gaps.length ? ` — не хватает: ${gaps.join(", ")}` : ""}</span>}
            <label className="ship-inline">Трек-номер<input aria-label={`Трек-номер ${shipment.number}`} autoComplete="off" maxLength={255} disabled={!ready}
              value={value} onChange={(event) => setTracks({ ...tracks, [shipment.id]: event.target.value })} /></label>
            <button type="button" className="primary" disabled={!ready || Boolean(busy) || !value.trim() || value.trim() === shipment.track_number}
              onClick={() => void mutate(`track:${shipment.id}`, () => intakeApi.saveTrack(shipment, value), `Трек сохранён: ${shipment.number} · ${value.trim()}`)}>Сохранить трек</button>
          </div>
        </div>;
      })}
    </section>
  </PageWindow>;
}
