import { PrinterSettingsCard } from "./LabelPrint";
import { TsdAccessCard } from "./TsdAccessCard";

/** «Оборудование» what the warehouse plugs into the ERP — the GRN label
 *  printer and the data collection terminals. */
export function EquipmentPage({ readOnly = false }: { readOnly?: boolean; admin?: boolean }) {
  return <section className="page equipment-page">
    <header className="page-head"><h1>Оборудование</h1></header>
    <p className="page-sub">Принтер этикеток ГРН и терминалы сбора данных склада.</p>
    <div className="equipment-cards">
      <PrinterSettingsCard readOnly={readOnly} />
      <TsdAccessCard />
    </div>
  </section>;
}
