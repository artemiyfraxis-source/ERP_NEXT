import { TsdAccessCard } from "./TsdAccessCard";

/** «Оборудование» what the warehouse plugs into the ERP — the data collection terminals. The label printer moved to
 *  «Настройка печати» on «Склад» and «Приёмка с производства» (owner 23.10.2026). */
export function EquipmentPage(_props: { readOnly?: boolean; admin?: boolean }) {
  return <section className="page equipment-page">
    <header className="page-head"><h1>Оборудование</h1></header>
    <p className="page-sub">Терминалы сбора данных склада. Принтер этикеток — кнопка «Настройка печати» на «Складе» и в «Приёмке с производства».</p>
    <div className="equipment-cards">
      <TsdAccessCard />
    </div>
  </section>;
}
