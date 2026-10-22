import { LABELS_OPTION, PrinterSettingsCard } from "./LabelPrint";
import { useStageOption } from "./StageOptions";
import { TsdAccessCard } from "./TsdAccessCard";

/** «Оборудование» (stage option warehouse.labels for the printer; the ТСД card is always on): what the warehouse plugs into the ERP — the GRN label
 *  printer and the data collection terminals. */
export function EquipmentPage({ readOnly = false }: { readOnly?: boolean; admin?: boolean }) {
  const labels = useStageOption(LABELS_OPTION);
  return <section className="page equipment-page">
    <header className="page-head"><h1>Оборудование</h1></header>
    <p className="page-sub">Принтер этикеток ГРН и терминалы сбора данных склада.</p>
    <div className="equipment-cards">
      {labels && <PrinterSettingsCard readOnly={readOnly} />}
      <TsdAccessCard />
    </div>
  </section>;
}
