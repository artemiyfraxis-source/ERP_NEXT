import { useState } from "react";
import "./labelDesigner.css";
import { type LabelKind } from "./LabelDesigner";
import { PrinterSettingsCard } from "./LabelPrint";
import { PageWindow } from "./PageWindow";

/* «Настройка печати» (owner 23.10.2026): the label printer and the label template, opened where labels are printed —
   «Склад» (партия) and «Приёмка с производства» (ГП); «Приёмка» can add the same button. */



/** The header button and its window; `kind` — whose label the template tab opens on. */
export function PrintSetupButton({ kind, readOnly = false }: { kind: LabelKind; readOnly?: boolean }) {
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" className="secondary" onClick={() => setOpen(true)}>Настройка печати</button>
    {open && <PrintSetupWindow kind={kind} readOnly={readOnly} close={() => setOpen(false)} />}
  </>;
}

/** «Настройка печати этикеток»: «Принтер» (agent, printer settings, «Пробная этикетка») and «Шаблон этикетки» (designer).
 *  Closing with unsaved template edits asks first. */
export const printerTemplatesUrl = (kind: LabelKind = "lot") => `?section=machines&printer=labels&templates=1&label_kind=${kind}`;
export function PrintSetupWindow({ kind, readOnly = false, close }: { kind: LabelKind; readOnly?: boolean; close: () => void }) {
  return <PageWindow label="Настройка печати этикеток" close={close} width={1100}><section className="panel print-setup">
    <header className="page-head"><h2>Настройка печати этикеток</h2><a className="secondary small" href={printerTemplatesUrl(kind)}>Шаблоны этикеток в карточке принтера</a><button type="button" className="secondary small" onClick={close}>Закрыть</button></header>
    <PrinterSettingsCard readOnly={readOnly}/>
  </section></PageWindow>;
}
