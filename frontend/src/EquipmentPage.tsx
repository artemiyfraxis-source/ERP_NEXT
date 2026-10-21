import { LABELS_OPTION, PrinterSettingsCard } from "./LabelPrint";
import { useStageOption } from "./StageOptions";
import { TsdAccessCard } from "./TsdAccessCard";

/** «Оборудование» (stage options warehouse.labels, warehouse.tsd): what the warehouse plugs into the ERP — the GRN label
 *  printer and the data collection terminals. Each card belongs to its own option; the section shows while either is on. */
export function EquipmentPage({ readOnly = false, admin = false }: { readOnly?: boolean; admin?: boolean }) {
  const labels = useStageOption(LABELS_OPTION);
  return <section className="page equipment-page">
    <header className="page-head"><h1>Оборудование</h1></header>
    <p className="page-sub">Принтер этикеток ГРН и терминалы сбора данных склада.</p>
    <div className="equipment-cards">
      {labels && <PrinterSettingsCard readOnly={readOnly} />}
      <TsdAccessCard admin={admin} />
    </div>
  </section>;
}
