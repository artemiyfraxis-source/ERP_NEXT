/** «Настройки закупки» — purchasing hybrid tab (stage option). Placeholder until the feature lands. */
export function PurchasingSettings({ readOnly = false }: { readOnly?: boolean } = {}) {
  return <section className="page"><p className="muted">«Настройки закупки»: в работе{readOnly ? " (просмотр)" : ""}.</p></section>;
}
