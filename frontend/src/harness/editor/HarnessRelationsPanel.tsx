import { routeCutReadiness } from "../manufacturing/route-cut-readiness";
import { CutDiagramPanel } from "./CutDiagramPanel";
import type { EditorCommand } from "./commands";
import { useMemo, useState } from "react";
import { HarnessCutListTable } from "../HarnessCutListPanel";
import { InfoHint } from "../InfoHint";
import type { HarnessDesignDocument } from "./model";
import { buildLiveCutList } from "./live-cut-list";
import type { resolveHarnessSelection } from "./harness-selection";

export function HarnessRelationsPanel({ document, projectId, harnessId, quantity, related, wholeNet, onWholeNet, onReveal, onClear, unsaved, hiddenCount, revision, onCommand, onOpenCut, showCut = false, sourceFingerprint }: {
  sourceFingerprint?: string;
  showCut?: boolean;
  onOpenCut?:()=>void;revision:number; onCommand:(command:EditorCommand)=>boolean;
  document: HarnessDesignDocument; projectId: string; harnessId: string; quantity: number;
  related: ReturnType<typeof resolveHarnessSelection>; wholeNet: boolean; onWholeNet: (value: boolean) => void;
  onReveal: (id?: string) => void; onClear: () => void; unsaved: boolean; hiddenCount: number;
}) {
  const cutReady=routeCutReadiness(document,sourceFingerprint,unsaved);
  const [onlyRelated, setOnlyRelated] = useState(false);
  const list = useMemo(() => buildLiveCutList(document, projectId, harnessId, quantity), [document, projectId, harnessId, quantity]);
  const displayed = onlyRelated ? { ...list, items: list.items.filter(item => related.rowIds.includes(item.wireId)) } : list;
  return <section className="he-relations" aria-label="Связанные провода">
    <header className="ui-section-heading"><strong>Провода · {related.wireIds.length}</strong><InfoHint>Подсветка показывает связи и не расширяет набор объектов для удаления или перемещения. «Вся цепь» проходит через электрические узлы, сохраняя независимость контактов разъёма. Карта рассчитана по текущему документу.</InfoHint></header>
    <div className="he-relations-actions">
      <label><input type="checkbox" checked={wholeNet} onChange={e => onWholeNet(e.target.checked)} />Вся цепь</label>
      <button type="button" className="ui-control" disabled={!related.wireIds.length} onClick={() => onReveal()}>На чертеже</button>
      <button type="button" className="ui-control" onClick={onClear}>Сброс</button>
    </div>
    {hiddenCount > 0 && <small role="status">На скрытых слоях: {hiddenCount}</small>}
    {related.unresolvedIds.length > 0 && <small role="status">Объект отсутствует в текущем документе.</small>}
    {showCut && !cutReady.ready && <p role="status">{cutReady.message}</p>}
    {showCut && cutReady.ready && <>{onOpenCut?<button type="button" className="ui-control" onClick={onOpenCut}>Схема резки / разделки</button>:<CutDiagramPanel sourceFingerprint={sourceFingerprint} document={document} quantity={quantity} revision={revision} unsaved={unsaved} relatedIds={related.rowIds} onReveal={onReveal} onCommand={onCommand}/>}
    <details><summary>Карта резки · {list.items.length}</summary>
      <div className="he-relations-actions"><label><input type="checkbox" checked={onlyRelated} onChange={e => setOnlyRelated(e.target.checked)} />Только связанные</label></div>
      <small role="status">{unsaved ? `Текущий документ · не сохранён · база r${revision}` : `Сохранённая ревизия r${revision}`}</small>
      <HarnessCutListTable cutList={displayed} onReveal={onReveal} highlightedIds={related.rowIds} />
    </details></>}
  </section>;
}
