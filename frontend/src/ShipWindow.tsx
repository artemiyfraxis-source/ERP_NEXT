import { useState } from "react";
import { PageWindow } from "./PageWindow";
import type { DispatchShipment } from "./productionIntakeApi";
import { Banners, ShipmentCard, useMutate, type CardRights } from "./ShipmentCard";
import { ShipmentDataWindow } from "./ShipmentData";

/* Окно «Отгружено» (владелец 29.10.2026): отправки проекта — готовность данных, печать PL/DN и этикеток, «Отгрузить»
   (упаковано, есть УПД, ТК и трек), «Разрешить без УПД» по ПИН уполномоченного, «Закрыть», откат «Отгружено» (администратор). */

const ORDER = ["Упакована", "Отгружена", "Собрана", "Новая", "Закрыта", "Отменена"];
const rank = (shipment: DispatchShipment) => { const index = ORDER.indexOf(shipment.status); return index < 0 ? ORDER.length : index; };

export function ShipWindow({ project, shipments, rights, reload, close }: {
  project: string; shipments: DispatchShipment[]; rights: CardRights; reload: () => Promise<void> | void; close: () => void;
}) {
  const { busy, error, notice, mutate } = useMutate(reload);
  const [dataFor, setDataFor] = useState<string | null>(null);
  const dataShipment = shipments.find((shipment) => shipment.id === dataFor) ?? null;
  const sorted = shipments.slice().sort((a, b) => rank(a) - rank(b) || (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
  return <PageWindow label={`Отгрузка ${project}`} close={close} width={1280}>
    <section className="panel intake-window intake-ship">
      <header className="page-head"><h2>Отгрузка · {project}</h2><button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      <Banners error={error} notice={notice} />
      {dataShipment && <ShipmentDataWindow key={dataShipment.id} shipment={dataShipment} close={() => setDataFor(null)} onSaved={reload} />}
      {sorted.length === 0 && <div className="empty">Отправок по проекту нет — сначала упакуйте принятое.</div>}
      {sorted.map((shipment) => <ShipmentCard key={`${shipment.id}:${shipment.version}`} shipment={shipment} rights={rights} busy={busy} mutate={mutate} openData={() => setDataFor(shipment.id)} />)}
    </section>
  </PageWindow>;
}
