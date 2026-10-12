import { useEffect, useMemo, useState } from "react";
import { failureText, shippingApi, type FinishedGood, type FromProductionRow } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDate } from "./format";
import { GOODS_STATES, GoodsStateBar, goodsPieces, sumPieces, type GoodsPieces } from "./FinishedGoodsStates";

/* «Склад → Готовая продукция» (владелец 12.10.2026): только просмотр — по проекту и КБ, сколько штук в каждом
   сквозном статусе. Принимают, упаковывают и отгружают на экране «Отгрузка». */

interface Row { good: FinishedGood; pieces: GoodsPieces; waiting: number }

const num = (value: string | null | undefined) => {
  const parsed = Number(String(value ?? "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
};
const keyOf = (project: string, index: string) => `${project.trim().toUpperCase()}\u0000${index.trim().toUpperCase()}`;

function openShipping(tab: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("section", "shipping");
  url.searchParams.delete("block");
  url.searchParams.set("tab", tab);
  window.history.pushState({}, "", url);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function ShippingLink({ tab, children }: { tab: string; children: string }) {
  return <a className="link-button" href={`?section=shipping&tab=${tab}`} onClick={(event) => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    openShipping(tab);
  }}>{children}</a>;
}

export function FinishedGoodsPanel() {
  const [goods, setGoods] = useState<FinishedGood[] | null>(null);
  const [incoming, setIncoming] = useState<FromProductionRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    Promise.all([shippingApi.goods(), shippingApi.fromProduction()])
      .then(([goodsResult, incomingResult]) => { if (live) { setGoods(goodsResult.goods ?? []); setIncoming(incomingResult.rows ?? []); } })
      .catch((reason) => { if (live) setError(failureText(reason, "Не удалось загрузить готовую продукцию")); });
    return () => { live = false; };
  }, []);

  const rows = useMemo<Row[]>(() => {
    const waiting = new Map(incoming.map((row) => [keyOf(row.project, row.item_index), num(row.remaining)]));
    return (goods ?? []).map((good) => ({ good, pieces: goodsPieces(good), waiting: waiting.get(keyOf(good.project, good.item_index)) ?? 0 }));
  }, [goods, incoming]);
  const byProject = useMemo(() => {
    const totals: Record<string, GoodsPieces> = {};
    for (const project of new Set(rows.map((row) => row.good.project))) totals[project] = sumPieces(rows.filter((row) => row.good.project === project).map((row) => row.pieces));
    return totals;
  }, [rows]);

  if (error) return <div className="error-banner" role="alert">{error}</div>;
  if (!goods) return <div className="empty">Загрузка…</div>;

  const count = (state: keyof GoodsPieces) => (row: Row) => row.pieces[state] > 0 ? row.pieces[state] : <span className="muted">—</span>;
  const columns: DataColumn<Row>[] = [
    { key: "project", title: "Проект", value: (row) => row.good.project, hidden: true },
    { key: "kb", title: "КБ", value: (row) => row.good.item_index, render: (row) => <strong>{row.good.item_index}</strong> },
    { key: "name", title: "Наименование", value: (row) => row.good.name, wrap: true },
    { key: "ordered", title: "Заказано", value: (row) => num(row.good.ordered), numeric: true },
    { key: "bar", title: "Статус КБ", value: (row) => row.pieces.accepted, render: (row) => <GoodsStateBar pieces={row.pieces} unit={row.good.unit} /> },
    ...GOODS_STATES.map((state) => ({ key: state.key, title: state.label, value: (row: Row) => row.pieces[state.key], numeric: true, render: count(state.key), hidden: true })),
    { key: "waiting", title: "Ждёт приёмки", value: (row) => row.waiting, numeric: true, hint: "Цех сдал, склад ещё не принял",
      render: (row) => row.waiting > 0 ? <span className="tone-warn">{row.waiting}</span> : <span className="muted">—</span> },
    { key: "grn", title: "ГРН", value: (row) => row.good.grn ?? "", render: (row) => row.good.grn ? <span className="goods-grn">{row.good.grn}</span> : <span className="muted">—</span> },
    { key: "received_at", title: "Последняя приёмка", value: (row) => row.good.received_at ?? "", render: (row) => formatDate(row.good.received_at) || <span className="muted">—</span> },
  ];
  const customer = (project: string) => rows.find((row) => row.good.project === project && row.good.customer)?.good.customer ?? "";
  return <section className="goods-panel" aria-label="Готовая продукция">
    <p className="goods-panel-head">
      <span>Принимают, упаковывают и отгружают в «Отгрузке»:</span>
      <ShippingLink tab="production">из производства</ShippingLink>
      <ShippingLink tab="stock">на складе</ShippingLink>
      <ShippingLink tab="shipments">отправки</ShippingLink>
    </p>
    <DataTable label="Готовая продукция" storageKey="stock-finished-goods" rows={rows} columns={columns} rowKey={(row) => row.good.id}
      groupBy={(row) => row.good.project}
      groupLabel={(project) => {
        const total = byProject[project];
        const parts = GOODS_STATES.map((state) => `${state.short} ${total?.[state.key] ?? 0}`).join(" · ");
        return `${project}${customer(project) ? ` · ${customer(project)}` : ""} — ${parts}`;
      }}
      presets={[{ label: "На складе", test: (row) => row.pieces.accepted > 0 }, { label: "Ждёт приёмки", test: (row) => row.waiting > 0 },
        { label: "Упаковано", test: (row) => row.pieces.packed > 0 }, { label: "В производстве", test: (row) => row.pieces.production > 0 }]}
      exportName="Готовая продукция" searchPlaceholder="Проект, КБ, наименование или ГРН"
      emptyText="Готовой продукции пока нет — КБ встают сюда, когда проект заведён." />
  </section>;
}
