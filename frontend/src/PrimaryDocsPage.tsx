import { useState } from "react";
import { urlParam } from "./navigation";
import { PurchasingDocChain } from "./PurchasingDocChain";
import { PurchasingDocSearch } from "./PurchasingDocSearch";
import { PurchasingInvoices } from "./PurchasingInvoices";
import { PurchasingUpd } from "./PurchasingUpd";
import { UpdInvoicesBlock } from "./UpdInvoicesBlock";

/* «Документация → Первичная документация» (правка Вити 4, владелец 23.10): документы закупки, что были вкладками
   «Листа закупки». Право то же — «Снабжение» (`procurement`). `?tab=` открывает вкладку, `&upd=<id>` — карточку УПД
   (openUpdCard из других экранов); старые ссылки `?section=procurement&tab=upd|invoices|chain|docsearch` приходят сюда
   через MOVED_TABS. */
type DocsTab = "upd" | "invoices" | "chain" | "docsearch";
const TABS: ReadonlyArray<[DocsTab, string]> = [
  ["upd", "УПД"], ["invoices", "Счета Kaiten"], ["chain", "Цепочка документов"], ["docsearch", "Поиск по документам"],
];

export function PrimaryDocsPage({ readOnly = false }: { readOnly?: boolean }) {
  const [tab, setTab] = useState<DocsTab>(() => TABS.find(([key]) => key === urlParam("tab"))?.[0] ?? "upd");
  const [updFocus, setUpdFocus] = useState<string | undefined>(() => urlParam("upd") || undefined);
  const pick = (next: DocsTab) => {
    setTab(next);
    if (next === "upd") setUpdFocus(undefined); // the tab itself opens the list, not the last card
  };
  const openUpd = (id: string) => { setTab("upd"); setUpdFocus(id); };
  return (
    <>
      <header className="page-head"><h1>Первичная документация</h1></header>
      {readOnly && <p className="page-sub">Первичная документация открыта вам только на просмотр.</p>}
      <div className="seg-tabs" role="tablist" aria-label="Первичная документация">
        {TABS.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : undefined} onClick={() => pick(key)}>{label}</button>
        ))}
      </div>
      {tab === "upd" && <PurchasingUpd readOnly={readOnly} focusId={updFocus}
        extraBlocks={(documentId) => <UpdInvoicesBlock documentId={documentId} readOnly={readOnly} />} />}
      {tab === "invoices" && <PurchasingInvoices readOnly={readOnly} onOpenUpd={openUpd} />}
      {tab === "chain" && <PurchasingDocChain readOnly={readOnly} onOpenUpd={openUpd} />}
      {tab === "docsearch" && <PurchasingDocSearch readOnly={readOnly} onOpenUpd={openUpd} />}
    </>
  );
}
