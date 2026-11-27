import { useState } from "react";
import { PageWindow } from "./PageWindow";
import type { DispatchShipment } from "./productionIntakeApi";
import { Banners, ShipmentCard, useMutate, type CardRights } from "./ShipmentCard";
import { ShipmentDataWindow } from "./ShipmentData";
import { compareShipments } from "./shipmentOrder";

/* Окно «Отгружено» (владелец 29.10.2026): отправки проекта — готовность данных, печать PL/DN и этикеток, «Отгрузить»
   (упаковано, есть УПД, ТК и трек), «Разрешить без УПД» по ПИН уполномоченного, откат «Отгружено» (администратор). */

export function ShipWindow({ project, shipments, rights, reload, close }: {
  project: string; shipments: DispatchShipment[]; rights: CardRights; reload: () => Promise<void> | void; close: () => void;
}) {
  const { busy, error, notice, mutate } = useMutate(reload);
  const [dataFor, setDataFor] = useState<string | null>(null);
  const dataShipment = shipments.find((shipment) => shipment.id === dataFor) ?? null;
  const sorted = shipments.slice().sort(compareShipments);
  return <PageWindow label={`Отгрузка ${project}`} close={close} width={1440}>
    <section className="panel intake-window intake-ship">
      <header className="page-head"><h2>Отгрузка · {project}</h2><button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      <Banners error={error} notice={notice} />
      {dataShipment && <ShipmentDataWindow key={dataShipment.id} shipment={dataShipment} canEdit={rights.canManage || rights.canWarehouse} close={() => setDataFor(null)} onSaved={reload} />}
      {sorted.length === 0 && <div className="empty">Отправок по проекту нет — сначала упакуйте принятое.</div>}
      {sorted.map((shipment) => <ShipmentCard key={`${shipment.id}:${shipment.version}`} shipment={shipment} rights={rights} busy={busy} mutate={mutate} openData={() => setDataFor(shipment.id)} />)}
    </section>
  </PageWindow>;
}
