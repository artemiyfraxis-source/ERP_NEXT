import { useEffect, useState } from "react";
import {
  fulfillmentApi,
  type FulfillmentLine,
  type FulfillmentLot,
  type FulfillmentRequest,
  type Principal,
} from "./api";
import { openPrintSheet } from "./print";

/* NAV-1: one request, two sections (SUPPLYMAP ISS_MODES_, owner 02.09.2026): «Запросы на комплектацию» picks and
   only reserves, «Выдача со списанием» hands the kit out and writes it off. «Готов к выдаче» belongs to the handout
   screen — the kit is packed and waits for the shop. Closed requests live in the archive of either screen. */
const MODES = {
  pick: {
    title: "Запросы на комплектацию",
    sub: "Набор комплекта под КБ: партии выбирает человек, склад на этом этапе только резервирует. Заявка ждёт поставки.",
    stages: ["Новая", "Недоукомплектован", "Укомплектован"],
    empty: "Заявок на комплектацию нет. Они появятся из состава производственного комплекта.",
  },
  give: {
    title: "Выдача со списанием",
    sub: "Передача собранного комплекта в цех: выдача списывает со склада, возврат принимает остаток и брак.",
    stages: ["Готов к выдаче", "КБ Выдана", "КБ Возвращена"],
    empty: "Заявок к выдаче нет. Сюда приходят укомплектованные заявки, отправленные на выдачу.",
  },
};
const ARCHIVE = ["Готово", "Отменена"];

export function FulfillmentPage({ principal, mode }: { principal: Principal; mode?: keyof typeof MODES }) {
  const [archive, setArchive] = useState(false);
  const [requests, setRequests] = useState<FulfillmentRequest[]>([]);
  const [lots, setLots] = useState<FulfillmentLot[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const canEdit = principal.permissions.fulfillment === "edit" && principal.roles.some((role) => ["engineer", "receiver", "admin"].includes(role));
  // NAV-SECTIONS: handing out writes off stock and has its own key; picking (reserve) stays on `fulfillment`.
  const canHandout = principal.permissions.handout === "edit" && principal.roles.some((role) => ["receiver", "admin"].includes(role));

  const screen = mode ? MODES[mode] : null;
  const stages = archive ? ARCHIVE : screen?.stages;
  const shown = stages ? requests.filter((request) => stages.includes(request.status)) : requests;

  async function reload() {
    setError("");
    try {
      const [requestResult, lotResult] = await Promise.all([
        fulfillmentApi.list(),
        canEdit ? fulfillmentApi.lots() : Promise.resolve({ lots: [] }),
      ]);
      setRequests(requestResult.requests);
      setLots(lotResult.lots);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить выдачу");
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => { void reload(); }, []);

  async function mutate(requestId: string, operation: () => Promise<unknown>) {
    if (busy) return;
    setBusy(requestId);
    setError("");
    try {
      await operation();
      await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Операция выдачи не выполнена");
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="page">
      <header className="page-head"><h1>{screen?.title ?? "Выдача в цех"}</h1><span className="page-count">{shown.length}</span>{mode && <button className={archive ? "secondary small active" : "secondary small"} onClick={() => setArchive((value) => !value)}>{archive ? "Активные" : "Архив"}</button>}</header>
      <p className="page-sub">{screen?.sub ?? "Партии выбирает человек. Заявка ждёт поставки, а в цех уходит только полностью собранный комплект."}</p>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="work-list fulfillment-list">
        {shown.map((request) => <FulfillmentCard
          key={`${request.id}:${request.version}`}
          request={request}
          lots={lots}
          canEdit={canEdit}
          canHandout={canHandout}
          busy={busy === request.id}
          mutate={mutate}
        />)}
      </div>
      {loaded && !shown.length && <div className="empty">{archive ? "В архиве пусто." : screen?.empty ?? "Заявок на выдачу нет. Они появятся из состава производственного комплекта."}</div>}
    </section>
  );
}

function FulfillmentCard({ request, lots, canEdit, canHandout, busy, mutate }: {
  request: FulfillmentRequest;
  lots: FulfillmentLot[];
  canEdit: boolean;
  canHandout: boolean;
  busy: boolean;
  mutate: (requestId: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [handoutLocation, setHandoutLocation] = useState(request.handout_location ?? "");
  const [returnLocation, setReturnLocation] = useState(request.return_location ?? "");
  const [printError, setPrintError] = useState("");
  const closed = ["КБ Выдана", "КБ Возвращена", "Готово", "Отменена"].includes(request.status);
  const hasIssuedLines = request.lines.some((line) => Number(line.issued) > 0);

  async function print(load: () => Promise<{ sheet: Parameters<typeof openPrintSheet>[0] }>) {
    setPrintError("");
    try {
      const { sheet } = await load();
      openPrintSheet(sheet);
    } catch (reason) {
      setPrintError(reason instanceof Error ? reason.message : "Не удалось сформировать бланк");
    }
  }

  return (
    <article className="panel fulfillment-card">
      <div className="procurement-title"><div><strong>{request.number}</strong><small>{request.purpose} · {request.requester}</small></div><span className="status">{request.status}</span></div>
      <div className="table-wrap fulfillment-lines"><table className="grid wide"><thead><tr><th>Позиция</th><th className="num">Потребность</th><th className="num">Резерв</th><th className="num">Выдано</th><th>Партия</th><th>Место</th><th /></tr></thead><tbody>
        {request.lines.map((line) => <FulfillmentLineRow key={line.id} request={request} line={line} lots={lots} canEdit={canEdit && !closed} busy={busy} mutate={mutate} />)}
      </tbody></table></div>
      <div className="fulfillment-actions">
        {canHandout && request.status === "Укомплектован" && <><label>Место выдачи<input value={handoutLocation} onChange={(event) => setHandoutLocation(event.target.value)} /></label><button className="secondary" disabled={busy || !handoutLocation.trim()} onClick={() => void mutate(request.id, () => fulfillmentApi.ready(request.id, request.version, handoutLocation))}>Готов к выдаче</button></>}
        {canHandout && request.status === "Готов к выдаче" && <button className="primary" disabled={busy} onClick={() => void mutate(request.id, () => fulfillmentApi.issue(request.id, request.version))}>Выдать комплект</button>}
        {canHandout && request.status === "КБ Выдана" && <><label>Место возврата<input value={returnLocation} onChange={(event) => setReturnLocation(event.target.value)} /></label><button className="secondary" disabled={busy || !returnLocation.trim()} onClick={() => void mutate(request.id, () => fulfillmentApi.returned(request.id, request.version, returnLocation))}>Комплект вернулся</button></>}
        {canEdit && !closed && <button className="link-button danger-link" disabled={busy} onClick={() => void mutate(request.id, () => fulfillmentApi.cancel(request.id, request.version))}>Отменить заявку</button>}
        <button className="secondary small" onClick={() => void print(() => fulfillmentApi.printSheet(request.id))}>Печать заявки</button>
        {hasIssuedLines && <button className="secondary small" onClick={() => void print(() => fulfillmentApi.returnSheet(request.id))}>Печать листа возврата</button>}
      </div>
      {printError && <div className="error-banner" role="alert">{printError}</div>}
      {canHandout && request.status === "КБ Возвращена" && <ReturnForm request={request} busy={busy} mutate={mutate} />}
    </article>
  );
}

function FulfillmentLineRow({ request, line, lots, canEdit, busy, mutate }: {
  request: FulfillmentRequest;
  line: FulfillmentLine;
  lots: FulfillmentLot[];
  canEdit: boolean;
  busy: boolean;
  mutate: (requestId: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const candidates = lots.filter((lot) => lot.unit.trim().toLocaleLowerCase("ru") === line.unit.trim().toLocaleLowerCase("ru"));
  const [lotId, setLotId] = useState(candidates[0]?.id ?? "");
  return <tr>
    <td className="text"><strong>{line.article || "—"}</strong><small>{line.name}</small></td>
    <td className="num">{line.requirement} {line.unit}</td><td className="num">{line.reserved}</td><td className="num">{line.issued}</td>
    <td>{line.lot ? `${line.lot.grn}${line.lot.package ? ` · ${line.lot.package}` : ""}` : canEdit ? <select aria-label={`Партия для ${line.name}`} value={lotId} onChange={(event) => setLotId(event.target.value)}><option value="">Выберите партию</option>{candidates.map((lot) => <option key={lot.id} value={lot.id}>{lot.article || lot.name} · {lot.grn} · {lot.available} {lot.unit} · {lot.warehouse} / {lot.storage_location || "без места"}</option>)}</select> : "Ждём поставки"}</td>
    <td>{line.lot ? line.lot.storage_location || "без места" : "—"}</td>
    <td>{canEdit && (line.lot ? <button className="link-button table-link" disabled={busy} onClick={() => void mutate(request.id, () => fulfillmentApi.unbind(request.id, line.id, request.version))}>Снять резерв</button> : <button className="secondary small" disabled={busy || !lotId} onClick={() => void mutate(request.id, () => fulfillmentApi.bind(request.id, line.id, request.version, lotId))}>Привязать</button>)}</td>
  </tr>;
}

type ReturnValues = {
  returned_good: string;
  defective_returned: string;
  defective_missing: string;
  storage_location: string;
};

function ReturnForm({ request, busy, mutate }: {
  request: FulfillmentRequest;
  busy: boolean;
  mutate: (requestId: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [values, setValues] = useState<Record<string, ReturnValues>>(() => Object.fromEntries(
    request.lines.map((line) => [line.id, {
      returned_good: line.returned_good,
      defective_returned: line.defective_returned,
      defective_missing: line.defective_missing,
      storage_location: line.lot?.storage_location ?? "",
    }]),
  ));

  function change(lineId: string, field: keyof ReturnValues, value: string) {
    setValues((current) => ({
      ...current,
      [lineId]: { ...current[lineId], [field]: value },
    }));
  }

  const payload = request.lines.map((line) => ({
    line_id: line.id,
    returned_good: values[line.id]?.returned_good || "0",
    defective_returned: values[line.id]?.defective_returned || "0",
    defective_missing: values[line.id]?.defective_missing || "0",
    storage_location: values[line.id]?.storage_location.trim() || null,
  }));

  return <div className="return-form">
    <h3>Разбор возвращённого комплекта</h3>
    <p>Укажи годное, два вида брака и новое место. Остаток от выданного будет зафиксирован как израсходованный.</p>
    {request.lines.map((line) => <div className="return-line" key={line.id}>
      <strong>{line.article || line.name}</strong><span>выдано {line.issued} {line.unit}</span>
      <label>Годное<input aria-label={`Годное ${line.name}`} type="number" min="0" step="any" value={values[line.id]?.returned_good ?? "0"} onChange={(event) => change(line.id, "returned_good", event.target.value)} /></label>
      <label>Брак возвращён<input aria-label={`Брак возвращён ${line.name}`} type="number" min="0" step="any" value={values[line.id]?.defective_returned ?? "0"} onChange={(event) => change(line.id, "defective_returned", event.target.value)} /></label>
      <label>Брак не возвращён<input aria-label={`Брак не возвращён ${line.name}`} type="number" min="0" step="any" value={values[line.id]?.defective_missing ?? "0"} onChange={(event) => change(line.id, "defective_missing", event.target.value)} /></label>
      <label>Место хранения<input aria-label={`Место хранения ${line.name}`} value={values[line.id]?.storage_location ?? ""} onChange={(event) => change(line.id, "storage_location", event.target.value)} /></label>
    </div>)}
    <button className="primary" disabled={busy} onClick={() => void mutate(request.id, () => fulfillmentApi.completeReturn(request.id, request.version, payload))}>Провести возврат</button>
  </div>;
}
