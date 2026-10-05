/** «Номенклатура» — purchasing hybrid tab (stage option). Placeholder until the feature lands. */
export function PurchasingItems({ readOnly = false }: { readOnly?: boolean } = {}) {
  return <section className="page"><p className="muted">«Номенклатура»: в работе{readOnly ? " (просмотр)" : ""}.</p></section>;
}
