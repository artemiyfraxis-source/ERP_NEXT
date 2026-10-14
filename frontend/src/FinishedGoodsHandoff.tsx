import { useState } from "react";
import { productionApi, type FinishedGood, type FinishedGoodsReadyBox } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDate } from "./format";
import { BoxName, boxTitle } from "./BoxLabel";

/* Коробки, принятые ОТК, — на склад с родословной (вынесено в «Приёмку с производства», владелец 14.10). */

export type Mutate = (key: string, operation: () => Promise<unknown>, message: string) => Promise<boolean>;

function boxCandidates(box: FinishedGoodsReadyBox, goods: FinishedGood[]) {
  const item = box.task_item.toUpperCase();
  return goods.filter((good) => good.project === box.project && (item === good.item_index.toUpperCase() || item.endsWith(`.${good.item_index.toUpperCase()}`) || item.endsWith(`-${good.item_index.toUpperCase()}`)));
}

/** Коробки, принятые ОТК, — на склад с родословной: отметить и принять разом. */
export function FinishedGoodsHandoff({ boxes, goods, busy, mutate }: { boxes: FinishedGoodsReadyBox[]; goods: FinishedGood[]; busy: string; mutate: Mutate }) {
  const [selected, setSelected] = useState<Record<string, string>>({});
  const target = (box: FinishedGoodsReadyBox) => selected[box.id] ?? boxCandidates(box, goods)[0]?.id ?? "";
  const columns: DataColumn<FinishedGoodsReadyBox>[] = [
    { key: "number", title: "Коробка", value: (box) => [box.code, box.number].filter(Boolean).join(" "), render: (box) => <BoxName box={box} /> },
    { key: "item", title: "Проект · КБ", value: (box) => `${box.project} · ${box.task_item}` },
    { key: "quantity", title: "Кол-во", value: (box) => box.quantity, numeric: true },
    { key: "confirmed", title: "Подтверждена", value: (box) => box.confirmed_at ?? "", render: (box) => formatDate(box.confirmed_at) || <span className="muted">—</span> },
    { key: "good", title: "КБ в готовой продукции", value: (box) => target(box), render: (box) => {
      const candidates = boxCandidates(box, goods);
      if (!candidates.length) return <span className="tone-danger">нет КБ проекта в готовой продукции</span>;
      return <select aria-label={`КБ для коробки ${boxTitle(box)}`} value={target(box)} onClick={(event) => event.stopPropagation()} onChange={(event) => setSelected((current) => ({ ...current, [box.id]: event.target.value }))}>
        {candidates.map((good) => <option key={good.id} value={good.id}>{good.project} · {good.item_index} · {good.name}</option>)}</select>;
    } },
  ];
  const handoff = (rows: FinishedGoodsReadyBox[]) => mutate("handoff", async () => {
    for (const box of rows) { const goodId = target(box); if (goodId) await productionApi.handoff(box.id, box.version, goodId); }
  }, `Принято на склад коробок: ${rows.filter(target).length}`);
  return <div className="ship-handoff">
    <h2>Коробки из цеха после ОТК <span className="page-count">{boxes.length}</span></h2>
    <DataTable label="Коробки из цеха" rows={boxes} columns={columns} rowKey={(box) => box.id} compactBelow={8}
      bulkActions={[{ label: "Принять на склад", run: handoff, disabled: (rows) => Boolean(busy) || !rows.some(target) }]}
      rowLabel={(box) => `коробка ${boxTitle(box)}`} />
  </div>;
}
