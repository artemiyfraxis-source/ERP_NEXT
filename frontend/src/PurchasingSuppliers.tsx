/** «Поставщики» — purchasing hybrid tab (stage option). Placeholder until the feature lands. */
export function PurchasingSuppliers({ readOnly = false }: { readOnly?: boolean } = {}) {
  return <section className="page"><p className="muted">«Поставщики»: в работе{readOnly ? " (просмотр)" : ""}.</p></section>;
}
