import { useState } from "react";
import { InfoHint } from "../InfoHint";
import type { HarnessDesignDocument } from "./model";
import type { resolveHarnessSelection } from "./harness-selection";
import { CutItemsTable } from "../panels/CutListPanel";
import { useHarnessDocument } from "../panels/documents-source";

/** The cut list of the side panel: asked from the server only while it is open. */
function RelatedCutList({ document, quantity, related, onlyRelated, onReveal }: {
  document: HarnessDesignDocument; quantity: number; related: readonly string[]; onlyRelated: boolean; onReveal: (id?: string) => void;
}) {
  const { data, error } = useHarnessDocument("cut", document, quantity);
  if (!data) return error ? <div className="error-banner" role="alert">{error}</div> : <div className="empty">Загрузка…</div>;
  const items = onlyRelated ? data.items.filter(item => related.includes(item.id)) : data.items;
  return <CutItemsTable items={items} activeId={related.find(id => items.some(item => item.id === id))} onSelect={item => onReveal(item.id)} />;
}

export function HarnessRelationsPanel({ document, quantity, related, wholeNet, onWholeNet, onReveal, onClear, hiddenCount, onOpenCut, showCut = false }: {
  showCut?: boolean; onOpenCut?: () => void;
  document: HarnessDesignDocument; quantity: number;
  related: ReturnType<typeof resolveHarnessSelection>; wholeNet: boolean; onWholeNet: (value: boolean) => void;
  onReveal: (id?: string) => void; onClear: () => void; hiddenCount: number;
}) {
  const [onlyRelated, setOnlyRelated] = useState(false);
  const [cutOpen, setCutOpen] = useState(false);
  return <section className="he-relations" aria-label="Связанные провода">
    <header className="ui-section-heading"><strong>Провода · {related.wireIds.length}</strong><InfoHint>Подсветка показывает связи и не расширяет набор объектов для удаления или перемещения. «Вся цепь» проходит через электрические узлы, сохраняя независимость контактов разъёма. Заготовки для резки считает сервер по текущему чертежу.</InfoHint></header>
    <div className="he-relations-actions">
      <label><input type="checkbox" checked={wholeNet} onChange={e => onWholeNet(e.target.checked)} />Вся цепь</label>
      <button type="button" className="ui-control" disabled={!related.wireIds.length} onClick={() => onReveal()}>На чертеже</button>
      <button type="button" className="ui-control" onClick={onClear}>Сброс</button>
    </div>
    {hiddenCount > 0 && <small role="status">На скрытых слоях: {hiddenCount}</small>}
    {related.unresolvedIds.length > 0 && <small role="status">Объект отсутствует в текущем документе.</small>}
    {showCut && <>{onOpenCut && <button type="button" className="ui-control" onClick={onOpenCut}>Схема резки / разделки</button>}
      <details open={cutOpen} onToggle={e => setCutOpen(e.currentTarget.open)}><summary>Карта резки</summary>
        <div className="he-relations-actions"><label><input type="checkbox" checked={onlyRelated} onChange={e => setOnlyRelated(e.target.checked)} />Только связанные</label></div>
        {cutOpen && <RelatedCutList document={document} quantity={quantity} related={related.rowIds} onlyRelated={onlyRelated} onReveal={onReveal} />}
      </details></>}
  </section>;
}
