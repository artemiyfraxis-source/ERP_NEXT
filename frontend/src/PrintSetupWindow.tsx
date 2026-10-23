import { useCallback, useRef, useState } from "react";
import "./labelDesigner.css";
import { LabelDesigner, LABEL_FORMATS, type LabelFormat, type LabelKind } from "./LabelDesigner";
import { PrinterSettingsCard, type PrintSettings } from "./LabelPrint";
import { PageWindow } from "./PageWindow";

/* «Настройка печати» (owner 23.10.2026): the label printer and the label template, opened where labels are printed —
   «Склад» (партия) and «Приёмка с производства» (ГП); «Приёмка» can add the same button. */

type Tab = "printer" | "template";

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
export function PrintSetupWindow({ kind, readOnly = false, close }: { kind: LabelKind; readOnly?: boolean; close: () => void }) {
  const [tab, setTab] = useState<Tab>("printer");
  // The designer stays mounted once opened: switching tabs keeps its unsaved edits.
  const [designerShown, setDesignerShown] = useState(false);
  const [usedFormat, setUsedFormat] = useState<LabelFormat | null>(null);
  const dirty = useRef(false);
  const [asking, setAsking] = useState(false);
  const onDirty = useCallback((value: boolean) => { dirty.current = value; }, []);
  const onSettings = useCallback((settings: PrintSettings) => {
    const key = `${settings.width_mm}x${settings.height_mm}`;
    setUsedFormat(LABEL_FORMATS.includes(key as LabelFormat) ? key as LabelFormat : null);
  }, []);
  const tryClose = () => { if (dirty.current) setAsking(true); else close(); };
  const show = (next: Tab) => { setTab(next); if (next === "template") setDesignerShown(true); };

  return <PageWindow label="Настройка печати этикеток" close={tryClose} width={1440}>
    <section className="panel print-setup">
      <header className="page-head"><h2>Настройка печати этикеток</h2>
        <div className="seg-tabs" role="tablist" aria-label="Настройка печати">
          {([["printer", "Принтер"], ["template", "Шаблон этикетки"]] as [Tab, string][]).map(([key, label]) =>
            <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : undefined} onClick={() => show(key)}>{label}</button>)}
        </div>
        <button type="button" className="secondary small" onClick={tryClose}>Закрыть</button>
      </header>
      <div hidden={tab !== "printer"}><PrinterSettingsCard readOnly={readOnly} onSettings={onSettings} /></div>
      {designerShown && <div hidden={tab !== "template"}><LabelDesigner initialKind={kind} usedFormat={usedFormat} readOnly={readOnly} onDirty={onDirty} /></div>}
    </section>
    {asking && <PageWindow label="Несохранённый шаблон" close={() => setAsking(false)} width={480}>
      <section className="panel ld-confirm">
        <h2>Закрыть без сохранения?</h2>
        <p>В шаблоне этикетки есть несохранённые изменения — они пропадут.</p>
        <div className="ld-confirm-actions">
          <button type="button" className="primary" onClick={() => { setAsking(false); setTab("template"); }}>Вернуться к шаблону</button>
          <button type="button" className="secondary" onClick={close}>Закрыть без сохранения</button>
        </div>
      </section>
    </PageWindow>}
  </PageWindow>;
}
