import { useEffect, useRef, useState } from "react";
import { ApiError, failureText, shippingApi, type ItemAddressee, type Shipment } from "./api";
import { isoDay } from "./Gantt";
import { canPack, kbKey, num, packAllowed, packable, permittedPack, round2 } from "./intakeStages";
import { PageWindow } from "./PageWindow";
import { compareShipments } from "./shipmentOrder";
import { deliveryGroups, intakeApi, type DispatchShipment, type IntakeRow } from "./productionIntakeApi";
import { termsText } from "./carriers";
import { Banners, ShipmentCard, useMutate, type CardRights, type Mutate } from "./ShipmentCard";
import { ShipmentDataWindow } from "./ShipmentData";

/* Окно «Упаковано» (владелец 29.10.2026, по аналогии с приёмкой — число в колонке открывает окно): принятое проекта —
   КБ и сколько упаковать; упаковывать можно только после решения менеджера «упаковывать». Ниже — отправки проекта на
   упаковке: короба и PL/DN («Сформировать», «Перевыпустить»), печать PL/DN и этикеток коробов, «Вернуть на склад» — их в
   строках таблицы нет (06.11). Данные отправки (адрес, получатель, ТК, УПД) вносят менеджер и склад. */

export function PackWindow({ project, rows, shipments, rights, reload, close }: {
  project: string; rows: IntakeRow[]; shipments: DispatchShipment[]; rights: CardRights;
  reload: () => Promise<void> | void; close: () => void;
}) {
  const { busy, error, notice, mutate } = useMutate(reload);
  const [dataFor, setDataFor] = useState<string | null>(null);
  const candidates = rows.filter((row) => packable(row) > 0 && row.good_id);
  const open = shipments.filter((shipment) => ["Новая", "Собрана", "Упакована"].includes(shipment.status)).sort(compareShipments);
  const dataShipment = shipments.find((shipment) => shipment.id === dataFor) ?? null;
  // После упаковки форма собирается заново по свежим остаткам.

  return <PageWindow label={`Упаковка ${project}`} close={close} width={1440}>
    <section className="panel intake-window intake-pack">
      <header className="page-head"><h2>Упаковка · {project}</h2><button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      <Banners error={error} notice={notice} />
      {dataShipment && <ShipmentDataWindow key={dataShipment.id} shipment={dataShipment} canEdit={rights.canManage || rights.canWarehouse} close={() => setDataFor(null)} onSaved={reload} />}
      {candidates.length > 0
        ? <PackForm project={project} candidates={candidates} rights={rights} busy={busy} mutate={mutate} reload={reload} />
        : <div className="empty">Принятого и не упакованного по проекту нет.</div>}
      {open.length > 0 && <div className="intake-ships">
        <h3>Отправки проекта на упаковке</h3>
        {open.map((shipment) => <ShipmentCard key={`${shipment.id}:${shipment.version}`} shipment={shipment} rights={rights} busy={busy} mutate={mutate} openData={() => setDataFor(shipment.id)} />)}
      </div>}
    </section>
  </PageWindow>;
}

function PackForm({ project, candidates, rights, busy, mutate, reload }: { project: string; candidates: IntakeRow[]; rights: CardRights; busy: string; mutate: Mutate; reload: () => Promise<void> | void }) {
  const [qty, setQty] = useState<Record<string, string>>({});
  const [marked, setMarked] = useState<Record<string, boolean>>({});
  const [deadline, setDeadline] = useState("");
  const [problem, setProblem] = useState("");
  const [addresseeVersion, setAddresseeVersion] = useState<number | null>(null);
  const [destinations, setDestinations] = useState<ItemAddressee[]>([]);
  const [deliveryReload, setDeliveryReload] = useState(0);
  const [refreshingDelivery, setRefreshingDelivery] = useState(true);
  // Ключ повтора живёт до успеха: повтор после обрыва связи вернёт уже заведённую отправку, а не заведёт вторую.
  const packKey = useRef(crypto.randomUUID());
  useEffect(() => {
    let live = true;
    setRefreshingDelivery(true); setAddresseeVersion(null); setProblem("");
    Promise.all([intakeApi.dispatch(project), shippingApi.itemAddressees(project)]).then(([result, delivery]) => {
      if (live) { setAddresseeVersion(result.addressee?.version ?? 0); setDestinations(delivery.items ?? []); }
    }).catch((reason) => { if (live) setProblem(failureText(reason, "Не удалось обновить доставку КБ. Повторите обновление.")); })
      .finally(() => { if (live) setRefreshingDelivery(false); });
    return () => { live = false; };
  }, [project, deliveryReload]);
  const chosen = candidates.filter((row) => canPack(row) && marked[kbKey(row)] && num(qty[kbKey(row)]) > 0);
  const total = round2(chosen.reduce((sum, row) => sum + num(qty[kbKey(row)]), 0));
  const groups = deliveryGroups(destinations.filter((item) => candidates.some((row) => row.item_index === item.item_index)));
  const chosenGroups = deliveryGroups(destinations.filter((item) => chosen.some((row) => row.item_index === item.item_index)));

  const pack = () => {
    setProblem("");
    const bad = candidates.find((row) => canPack(row) && qty[kbKey(row)]?.trim() && !(num(qty[kbKey(row)]) > 0));
    if (bad) { setProblem(`${bad.item_index}: введите число больше нуля`); return; }
    if (!chosen.length) { setProblem("Отметьте КБ и укажите количество"); return; }
    if (chosenGroups.length > 1) { setProblem("У выбранных КБ разные данные доставки. Упакуйте каждую группу отдельной отправкой; введённые количества сохранены."); return; }
    const over = chosen.find((row) => num(qty[kbKey(row)]) > permittedPack(row));
    if (over) { setProblem(`${over.item_index}: можно упаковать не больше ${permittedPack(over)}`); return; }
    const lines = chosen.map((row) => ({ good_id: String(row.good_id), version: row.good_version ?? 0, quantity: String(qty[kbKey(row)]).replace(",", ".") }));
    void mutate(`pack:${project}`, async () => {
      // The server resolves all selected KBs and freezes the destination for this consignment.
      let result;
      try { result = await shippingApi.projectShip({
        project, addressee_version: addresseeVersion ?? 0, lines, ship: false,
        ...(rights.canManage ? { documents: { upd_number: "", upd_date: null, deadline: deadline || null } } : {}),
      }, packKey.current); } catch (reason) {
        if (reason instanceof ApiError && [409, 422].includes(reason.status)) {
          setProblem("Проверьте обновлённые остатки, разрешения и доставку; ваш ввод сохранён.");
          setAddresseeVersion(null);
          const [, refreshed, delivery] = await Promise.all([reload(), intakeApi.dispatch(project), shippingApi.itemAddressees(project)]);
          setAddresseeVersion(refreshed.addressee?.version ?? 0);
          setDestinations(delivery.items ?? []);
        }
        throw reason;
      }
      packKey.current = crypto.randomUUID();
      setMarked({}); setQty({});
      return result;
    }, (outcome) => {
      const { shipment } = outcome as { shipment: Shipment };
      return `Упаковано: ${shipment.number}${shipment.pl_number ? ` · ${shipment.pl_number}` : ""}`;
    });
  };

  return <>
        {groups.length > 0 && <div className="item-delivery-groups" aria-label="Группы доставки">
          {groups.map((group, index) => <div key={index}>
            <strong>{groups.length > 1 ? `Доставка ${index + 1} · ` : "Доставка · "}{group.items.join(", ")}</strong>
            <span>{[group.delivery.receiver, group.delivery.contact, group.delivery.phone, group.delivery.shipping_point,
              termsText(group.delivery.terms), group.delivery.carrier, group.delivery.upd_type].filter(Boolean).join(" · ") || "Не задана"}</span>
          </div>)}
        </div>}
        <div className="table-scroll"><table className="grid compact intake-window-table intake-pack-table">
          <thead><tr><th aria-label="Отметка" /><th>Позиция</th><th className="grow">Наименование</th><th className="num">Заказано</th><th className="num">Упаковано</th><th className="num">Недобор</th><th>ГРН</th></tr></thead>
          <tbody>{candidates.map((row) => {
            const key = kbKey(row);
            const allowed = canPack(row) && rights.canEdit;
            const typed = qty[key] ?? "";
            const value = num(typed);
            const limit = permittedPack(row);
            const invalid = typed.trim() !== "" && (!Number.isFinite(Number(typed.replace(",", "."))) || value <= 0 || value > limit);
            const reason = typed.trim() === "" ? "" : value <= 0 || !Number.isFinite(Number(typed.replace(",", "."))) ? "Введите число больше нуля" : value > limit ? `Можно не больше ${limit}` : "";
            // Недобор — чего не хватает до заказанного после уже упакованного и вводимого сейчас.
            const shortage = Math.max(round2(num(row.needed) - num(row.packed) - (invalid ? 0 : value)), 0);
            const grn = (row.acts ?? []).map((act) => act.grn).filter(Boolean).join(", ") || row.grn || "";
            return <tr key={key} className={allowed ? undefined : "ship-line-off"}>
              <td><input type="checkbox" aria-label={`Упаковать ${row.item_index}`} disabled={!allowed} checked={allowed && Boolean(marked[key])} onChange={(event) => setMarked({ ...marked, [key]: event.target.checked })} /></td>
              <td><strong>{row.project !== project ? `${row.project} · ` : ""}{row.item_index}</strong></td>
              <td className="grow"><span className="intake-clip intake-name" title={row.name}>{row.name}</span></td>
              <td className="num">{row.needed} {row.unit}</td>
              <td className="num">{allowed
                ? <span className="intake-pack-cell">
                  <input className={invalid ? "ship-qty invalid" : "ship-qty"} aria-label={`Сколько упаковать ${row.item_index}`} aria-invalid={invalid} aria-describedby={`pack-hint-${key}`} type="text" inputMode="decimal" data-max={limit} value={typed}
                    placeholder={`до ${limit}`}
                    onChange={(event) => { setQty({ ...qty, [key]: event.target.value }); setMarked({ ...marked, [key]: num(event.target.value) > 0 }); }} />
                  <small className={invalid ? "tone-danger" : "muted"} id={`pack-hint-${key}`}>{invalid ? reason : `уже упаковано ${row.packed}, можно ещё ${limit}`}</small>
                </span>
                : <span className="muted" title="Упаковать можно после решения менеджера «упаковывать»">{packAllowed(row) ? row.packed : "ждёт решения менеджера"}</span>}</td>
              <td className={shortage > 0 ? "num tone-warn" : "num"} aria-label={`Недобор ${row.item_index}`}>{shortage}</td>
              <td>{grn || <span className="muted">—</span>}</td>
            </tr>;
          })}</tbody>
        </table></div>
        {rights.canEdit && <div className="intake-window-actions">
          {rights.canManage && <label className="ship-inline">Отгрузить до<input aria-label={`Отгрузить до: проект ${project}`} type="date" min={isoDay(new Date())} value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>}
          {problem && <span className="tone-danger" role="alert">{problem}</span>}
          <button type="button" className="secondary" disabled={Boolean(busy) || refreshingDelivery} onClick={() => setDeliveryReload((value) => value + 1)}>
            {refreshingDelivery ? "Обновляем доставку…" : "Обновить доставку КБ"}</button>
          <button type="button" className="primary" disabled={!chosen.length || Boolean(busy) || addresseeVersion === null} onClick={pack}>
            {busy === `pack:${project}` ? "Упаковываем…" : `Упаковать · ${total} шт`}</button>
        </div>}
  </>;
}
