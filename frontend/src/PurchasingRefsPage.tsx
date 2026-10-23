import { useState } from "react";
import { openUpdCard, urlParam } from "./navigation";
import { PurchasingCatalog } from "./PurchasingCatalog";
import { PurchasingItems } from "./PurchasingItems";
import { PurchasingSettings } from "./PurchasingSettings";
import { PurchasingSuppliers } from "./PurchasingSuppliers";

/* «Справочники → Закупка» (правка Вити 4, владелец 23.10): справочники закупки, что были вкладками «Листа закупки».
   Право то же — «Снабжение» (`procurement`); на просмотр — только чтение. `?tab=` открывает вкладку, старые ссылки
   `?section=procurement&tab=directory|items|catalog|settings` приходят сюда через MOVED_TABS. */
type RefsTab = "directory" | "items" | "catalog" | "settings";
const TABS: ReadonlyArray<[RefsTab, string]> = [
  ["directory", "Поставщики"], ["items", "Номенклатура"], ["catalog", "Каталог закупок"], ["settings", "Настройки"],
];

export function PurchasingRefsPage({ readOnly = false }: { readOnly?: boolean }) {
  const [tab, setTab] = useState<RefsTab>(() => TABS.find(([key]) => key === urlParam("tab"))?.[0] ?? "directory");
  return (
    <>
      <header className="page-head"><h1>Закупка</h1></header>
      {readOnly && <p className="page-sub">Справочники закупки открыты вам только на просмотр.</p>}
      <div className="seg-tabs" role="tablist" aria-label="Справочники закупки">
        {TABS.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : undefined} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>
      {tab === "directory" && <PurchasingSuppliers readOnly={readOnly} onOpenUpd={openUpdCard} />}
      {tab === "items" && <PurchasingItems readOnly={readOnly} onOpenUpd={openUpdCard} />}
      {tab === "catalog" && <PurchasingCatalog readOnly={readOnly} onOpenUpd={openUpdCard} />}
      {tab === "settings" && <PurchasingSettings readOnly={readOnly} />}
    </>
  );
}
