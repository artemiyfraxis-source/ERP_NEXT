import { useEffect, useState } from "react";
import { failureText, vnaChecksApi, type VnaCheckDue, type VnaCheckRecord, type VnaChecksSnapshot } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDateTime, formatNumber } from "./format";

const dueTone = (due: VnaCheckDue) => due.status === "ok" ? "tone-ok" : due.status === "failed" ? "tone-danger" : "tone-warn";
const localDay = (value: string | null) => value ? new Date(value).toLocaleDateString("ru-RU") : "";
const num = (value: number | null, digits: number) => value === null ? "" : formatNumber(value, { digits, minDigits: digits });
const confidenceText = (row: VnaCheckRecord) => row.confidence_passed === null ? "" : row.confidence_passed ? "Годен" : "Не годен";
const kindShort: Record<VnaCheckRecord["kind"], string> = {
  session: "Калибровка: доверительный тест и LOAD", flex: "Изгиб тракта", control_standard: "Контрольная мера", kit_standards: "Меры 6650F27",
};

const columns: DataColumn<VnaCheckRecord>[] = [
  { key: "performed_at", title: "Дата", value: (row) => row.performed_at, render: (row) => formatDateTime(row.performed_at), width: 140 },
  { key: "kind", title: "Проверка", value: (row) => kindShort[row.kind], filter: true, width: 230 },
  { key: "passed", title: "Итог", value: (row) => row.passed ? "Годен" : "Не годен", filter: true, width: 90,
    render: (row) => <span className={row.passed ? "tone-ok" : "tone-danger"}>{row.passed ? "Годен" : "Не годен"}</span> },
  { key: "confidence", title: "Довер. тест", value: (row) => confidenceText(row), render: (row) => confidenceText(row), width: 96 },
  { key: "confidence_tx", title: "Тест: передача, дБ", value: (row) => row.confidence_tx_dev_db, numeric: true, width: 112, render: (row) => num(row.confidence_tx_dev_db, 3) },
  { key: "confidence_rx", title: "Тест: отражение, дБ", value: (row) => row.confidence_rx_dev_db, numeric: true, width: 118, render: (row) => num(row.confidence_rx_dev_db, 3) },
  { key: "load_p1", title: "LOAD порт 1, КСВН", value: (row) => row.load_p1_vswr, numeric: true, width: 112, render: (row) => num(row.load_p1_vswr, 3) },
  { key: "load_p2", title: "LOAD порт 2, КСВН", value: (row) => row.load_p2_vswr, numeric: true, width: 112, render: (row) => num(row.load_p2_vswr, 3) },
  { key: "standard", title: "Мера", value: (row) => row.standard_inventory ?? "", render: (row) => row.standard_inventory ?? "", width: 84 },
  { key: "vswr_dev", title: "Откл. КСВН", value: (row) => row.vswr_dev, numeric: true, width: 92, render: (row) => num(row.vswr_dev, 4) },
  { key: "vswr_limit", title: "U КСВН", value: (row) => row.vswr_limit, numeric: true, width: 80, render: (row) => num(row.vswr_limit, 4) },
  { key: "s21_dev", title: "Откл. S21, дБ", value: (row) => row.s21_dev_db, numeric: true, width: 96, render: (row) => num(row.s21_dev_db, 3) },
  { key: "kit_ratio", title: "Доля допуска", value: (row) => row.kit_ratio, numeric: true, width: 96, render: (row) => num(row.kit_ratio, 2) },
  { key: "vna_t", title: "Т ВАЦ, °C", value: (row) => row.vna_temperature_c, numeric: true, width: 80, render: (row) => num(row.vna_temperature_c, 1), hidden: true },
  { key: "acm_t", title: "Т АКМ, °C", value: (row) => row.acm_temperature_c, numeric: true, width: 80, render: (row) => num(row.acm_temperature_c, 1), hidden: true },
  { key: "acm", title: "АКМ, зав. №", value: (row) => row.acm_serial, width: 100, hidden: true },
  { key: "profile", title: "Профиль", value: (row) => row.profile, width: 220, hidden: true },
  { key: "reference", title: "Эталонная кривая", value: (row) => row.reference, width: 220, hidden: true },
  { key: "operator", title: "Оператор", value: (row) => row.operator, width: 110 },
  { key: "actor", title: "Записал", value: (row) => row.actor, width: 130, hidden: true },
  { key: "note", title: "Примечание", value: (row) => row.note, grow: true, minWidth: 160, wrap: true },
];

/** «Журнал проверок ВАЦ» в карточке прибора (СТО ФРАК-002-2026, разд. 14, форма В.1): пишет только программа замера. */
export function InstrumentVnaChecks({ instrumentId, refreshKey }: { instrumentId: string; refreshKey: number }) {
  const [snapshot, setSnapshot] = useState<VnaChecksSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    vnaChecksApi.get(instrumentId).then((result) => {
      if (!Array.isArray(result?.due) || !Array.isArray(result?.records)) throw new Error("Журнал проверок ВАЦ не получен");
      if (active) setSnapshot(result);
    }).catch((reason) => { if (active) setError(failureText(reason, "Не удалось загрузить журнал проверок ВАЦ")); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [instrumentId, refreshKey, attempt]);

  if (snapshot && !snapshot.applicable && !loading && !error) return null;
  return <section className="instrument-vna-checks" aria-label="Журнал проверок ВАЦ" aria-busy={loading}>
    <h3>Журнал проверок ВАЦ</h3>
    {loading && <p className="empty">Загрузка журнала проверок…</p>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {error && !loading && <button type="button" className="secondary small" onClick={() => setAttempt((value) => value + 1)}>Обновить журнал</button>}
    {snapshot && !loading && <>
      <div className="instrument-vna-due">
        {snapshot.due.map((due) => <div className="instrument-vna-due-item" key={due.kind}>
          <strong>{due.label}</strong>
          <span className={dueTone(due)}>{due.status === "ok" ? `В срок, до ${localDay(due.due_at)}` : due.status_label}</span>
          {due.last_at && <span className="muted-text">Последняя: {formatDateTime(due.last_at)}</span>}
        </div>)}
      </div>
      <DataTable label="Записи журнала проверок ВАЦ" rows={snapshot.records} columns={columns} rowKey={(row) => row.id} pageSize={50}
        storageKey="instrument-vna-checks"
        emptyText="Проверок ещё нет — выполните калибровку и проверки в программе замера (пункты 2, 3, 11–13)." />
    </>}
  </section>;
}
