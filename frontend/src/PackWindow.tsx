import { useEffect, useRef, useState } from "react";
import { shippingApi, type Shipment } from "./api";
import { isoDay } from "./Gantt";
import { canPack, kbKey, num, packable, round2 } from "./intakeStages";
import { PageWindow } from "./PageWindow";
import { intakeApi, type DispatchShipment, type IntakeRow } from "./productionIntakeApi";
import { Banners, ShipmentCard, useMutate, type CardRights, type Mutate } from "./ShipmentCard";
import { ShipmentDataWindow } from "./ShipmentData";

/* Окно «Упаковано» (владелец 29.10.2026, по аналогии с приёмкой — число в колонке открывает окно): принятое проекта —
   КБ и сколько упаковать; упаковывать можно только после решения менеджера «упаковывать». Ниже — отправки проекта на
   упаковке: короба и PL/DN («Сформировать», «Перевыпустить»), печать PL/DN и этикеток коробов, «Вернуть на склад». */

export function PackWindow({ project, rows, shipments, rights, reload, close }: {
  project: string; rows: IntakeRow[]; shipments: DispatchShipment[]; rights: CardRights;
  reload: () => Promise<void> | void; close: () => void;
}) {
  const { busy, error, notice, mutate } = useMutate(reload);
  const [dataFor, setDataFor] = useState<string | null>(null);
  const candidates = rows.filter((row) => packable(row) > 0 && row.good_id);
  const open = shipments.filter((shipment) => ["Новая", "Собрана", "Упакована"].includes(shipment.status));
  const dataShipment = shipments.find((shipment) => shipment.id === dataFor) ?? null;
  // После упаковки форма собирается заново по свежим остаткам.
  const formKey = candidates.map((row) => `${kbKey(row)}:${packable(row)}:${row.pack_decided ? 1 : 0}`).join("|");

  return <PageWindow label={`Упаковка ${project}`} close={close} width={1280}>
    <section className="panel intake-window intake-pack">
      <header className="page-head"><h2>Упаковка · {project}</h2><button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
      <Banners error={error} notice={notice} />
      {dataShipment && <ShipmentDataWindow key={dataShipment.id} shipment={dataShipment} close={() => setDataFor(null)} onSaved={reload} />}
      {candidates.length > 0
        ? <PackForm key={formKey} project={project} candidates={candidates} rights={rights} busy={busy} mutate={mutate} />
        : <div className="empty">Принятого и не упакованного по проекту нет.</div>}
      {open.length > 0 && <div className="intake-ships">
        <h3>Отправки проекта на упаковке</h3>
        {open.map((shipment) => <ShipmentCard key={`${shipment.id}:${shipment.version}`} shipment={shipment} rights={rights} busy={busy} mutate={mutate} openData={() => setDataFor(shipment.id)} />)}
      </div>}
    </section>
  </PageWindow>;
}

function PackForm({ project, candidates, rights, busy, mutate }: { project: string; candidates: IntakeRow[]; rights: CardRights; busy: string; mutate: Mutate }) {
  const [qty, setQty] = useState<Record<string, string>>(() => Object.fromEntries(candidates.map((row) => [kbKey(row), String(packable(row))])));
  const [marked, setMarked] = useState<Record<string, boolean>>(() => Object.fromEntries(candidates.map((row) => [kbKey(row), canPack(row)])));
  const [deadline, setDeadline] = useState("");
  const [problem, setProblem] = useState("");
  const [addresseeVersion, setAddresseeVersion] = useState<number | null>(null);
  // Ключ повтора живёт до успеха: повтор после обрыва связи вернёт уже заведённую отправку, а не заведёт вторую.
  const packKey = useRef(crypto.randomUUID());
  useEffect(() => {
    let live = true;
    intakeApi.dispatch(project).then((result) => { if (live) setAddresseeVersion(result.addressee?.version ?? 0); })
      .catch(() => { if (live) setAddresseeVersion(0); });
    return () => { live = false; };
  }, [project]);
  const chosen = candidates.filter((row) => canPack(row) && marked[kbKey(row)] && num(qty[kbKey(row)]) > 0);
  const total = round2(chosen.reduce((sum, row) => sum + num(qty[kbKey(row)]), 0));

  const pack = () => {
    setProblem("");
    const over = chosen.find((row) => num(qty[kbKey(row)]) > packable(row));
    if (over) { setProblem(`${over.item_index}: на складе только ${packable(over)}`); return; }
    const lines = chosen.map((row) => ({ good_id: String(row.good_id), version: row.good_version ?? 0, quantity: String(qty[kbKey(row)]).replace(",", ".") }));
    void mutate(`pack:${project}`, async () => {
      // Адресат не копируется в отправку: пустые поля идут за карточкой проекта, пока отправка открыта.
      const result = await shippingApi.projectShip({
        project, addressee_version: addresseeVersion ?? 0, lines, ship: false,
        ...(rights.canManage ? { documents: { upd_number: "", upd_date: null, deadline: deadline || null } } : {}),
      }, packKey.current);
      packKey.current = crypto.randomUUID();
      return result;
    }, (outcome) => {
      const { shipment } = outcome as { shipment: Shipment };
      return `Упаковано: ${shipment.number}${shipment.pl_number ? ` · ${shipment.pl_number}` : ""}`;
    });
  };

  return <>
        <table className="grid compact intake-window-table">
          <thead><tr><th aria-label="Отметка" /><th>КБ</th><th className="grow">Наименование</th><th className="num">Принято</th><th className="num">Упаковано</th><th className="num">Можно упаковать</th><th className="num">Упаковать, шт</th></tr></thead>
          <tbody>{candidates.map((row) => {
            const key = kbKey(row);
            const allowed = canPack(row) && rights.canEdit;
            const tooMuch = num(qty[key]) > packable(row);
            return <tr key={key} className={allowed ? undefined : "ship-line-off"}>
              <td><input type="checkbox" aria-label={`Упаковать ${row.item_index}`} disabled={!allowed} checked={allowed && Boolean(marked[key])} onChange={(event) => setMarked({ ...marked, [key]: event.target.checked })} /></td>
              <td><strong>{row.item_index}</strong></td>
              <td className="grow"><span className="intake-clip intake-name" title={row.name}>{row.name}</span></td>
              <td className="num">{row.accepted}</td><td className="num">{row.packed}</td><td className="num"><strong>{packable(row)}</strong></td>
              <td className="num">{allowed
                ? <input className={tooMuch ? "ship-qty invalid" : "ship-qty"} aria-label={`Сколько упаковать ${row.item_index}`} type="number" min="0" max={packable(row)} step="any" value={qty[key] ?? ""}
                  onChange={(event) => { setQty({ ...qty, [key]: event.target.value }); setMarked({ ...marked, [key]: num(event.target.value) > 0 }); }} />
                : <span className="muted" title="Упаковать можно после решения менеджера «упаковывать»">{row.pack_decided ? "—" : "ждёт решения менеджера"}</span>}</td>
            </tr>;
          })}</tbody>
        </table>
        {rights.canEdit && <div className="intake-window-actions">
          {rights.canManage && <label className="ship-inline">Отгрузить до<input aria-label={`Отгрузить до: проект ${project}`} type="date" min={isoDay(new Date())} value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>}
          {problem && <span className="tone-danger" role="alert">{problem}</span>}
          <button type="button" className="primary" disabled={!chosen.length || Boolean(busy) || addresseeVersion === null} onClick={pack}>
            {busy === `pack:${project}` ? "Упаковываем…" : `Упаковать · ${total} шт`}</button>
        </div>}
  </>;
}
