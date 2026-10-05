/** «Запросы КП» — purchasing hybrid tab (stage option). Placeholder until the feature lands. */
export function PurchasingRfq({ readOnly = false }: { readOnly?: boolean } = {}) {
  return <section className="page"><p className="muted">«Запросы КП»: в работе{readOnly ? " (просмотр)" : ""}.</p></section>;
}
