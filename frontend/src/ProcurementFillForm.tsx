import { useEffect, useState } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { procurementApi, type ProcurementLine } from "./api";

/* «Заполнить данные» — SUPPLYMAP portalPurchaseInWork + portalSavePurchaseFacts: все свои строки «В работе»
   одной таблицей. Правится артикул, наименование расчёта, наименование с сайта, ссылка, поставщик, факт.
   кол-во и ед.изм.; остальное — из заявки для контекста. Сохраняются только отмеченные строки («В обработку»),
   строка отмечается сама, как только начнёшь её править. */

type Draft = {
  design_article: string;
  calculated_name: string;
  site_name: string;
  link: string;
  supplier: string;
  actual_quantity: string;
  actual_unit: string;
};

function draftOf(line: ProcurementLine): Draft {
  return {
    design_article: line.design_article ?? "",
    calculated_name: line.calculated_name,
    site_name: line.site_name ?? "",
    link: line.link ?? "",
    supplier: line.supplier ?? "",
    actual_quantity: line.actual_quantity ?? "",
    actual_unit: line.actual_unit ?? "",
  };
}

export function ProcurementFillForm({ units, onSaved, onClose }: {
  units: string[];
  onSaved: (message: string) => void;
  onClose: () => void;
}) {
  const [lines, setLines] = useState<ProcurementLine[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [marked, setMarked] = useState<Set<string>>(new Set());
  const [missing, setMissing] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    try {
      const result = await procurementApi.myInWork();
      setLines(result.lines);
      setDrafts(Object.fromEntries(result.lines.map((line) => [line.id, draftOf(line)])));
      setMarked(new Set());
      setMissing(new Set());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Строки не загрузились");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  function edit(line: ProcurementLine, key: keyof Draft, value: string) {
    setDrafts((current) => ({ ...current, [line.id]: { ...current[line.id], [key]: value } }));
    setMarked((current) => new Set(current).add(line.id));
  }

  function toggle(line: ProcurementLine, checked: boolean) {
    setMarked((current) => {
      const next = new Set(current);
      if (checked) next.add(line.id); else next.delete(line.id);
      return next;
    });
  }

  async function save() {
    const rows = lines.filter((line) => marked.has(line.id));
    if (!rows.length) {
      setError("Отметь галочкой строки, которые сохраняем (☑ В обработку).");
      return;
    }
    const noSupplier = rows.filter((line) => !drafts[line.id].supplier.trim());
    setMissing(new Set(noSupplier.map((line) => line.id)));
    if (noSupplier.length) {
      setError(`Без поставщика строка не уйдёт в запрос. Заполни «Поставщик»: ${noSupplier.map((line) => line.legacy_id).join(", ")}`);
      return;
    }
    setSaving(true);
    setError("");
    try {
      const result = await procurementApi.saveFactsBatch(rows.map((line) => {
        const draft = drafts[line.id];
        return {
          line_id: line.id,
          legacy_id: line.legacy_id,
          version: line.version,
          design_article: draft.design_article.trim() || null,
          calculated_name: draft.calculated_name.trim() || null,
          site_name: draft.site_name.trim() || null,
          link: draft.link.trim() || null,
          supplier: draft.supplier.trim(),
          actual_quantity: draft.actual_quantity.trim() || null,
          actual_unit: draft.actual_unit.trim() || null,
        };
      }));
      const parts = [`Сохранено строк: ${result.saved}`];
      if (result.skipped.length) parts.push(`пропущено (строка изменилась): ${result.skipped.join(", ")}`);
      if (result.ready) parts.push(`готовы к запросу: ${result.ready}`);
      if (result.failed.length) setError(result.failed.map((item) => `${item.legacy_id}: ${item.error}`).join("; "));
      onSaved(`${parts.join(" · ")}. Остальные строки не тронуты.`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Данные не сохранены");
    } finally {
      setSaving(false);
    }
  }

  const input = (key: keyof Draft, label: string, type = "text") => (line: ProcurementLine) => (
    <input
      aria-label={`${label} ${line.legacy_id}`}
      type={type}
      aria-invalid={key === "supplier" && missing.has(line.id) ? true : undefined}
      style={key === "supplier" && missing.has(line.id) ? { borderColor: "var(--destructive, #c0392b)" } : undefined}
      value={drafts[line.id]?.[key] ?? ""}
      onChange={(event) => edit(line, key, event.target.value)}
    />
  );

  const columns: DataColumn<ProcurementLine>[] = [
    {
      key: "mark", title: "В обработку", value: (line) => (marked.has(line.id) ? "да" : ""),
      render: (line) => <input type="checkbox" aria-label={`В обработку ${line.legacy_id}`} checked={marked.has(line.id)} onChange={(event) => toggle(line, event.target.checked)} />,
    },
    { key: "legacy_id", title: "System ID", value: (line) => line.legacy_id },
    { key: "task_type", title: "Тип задачи", value: (line) => line.task_type ?? "" },
    { key: "priority", title: "Приоритет", value: (line) => line.priority },
    { key: "category", title: "Категория", value: (line) => line.category },
    { key: "item_type", title: "Тип", value: (line) => line.item_type },
    { key: "design_article", title: "Артикул КД", value: (line) => drafts[line.id]?.design_article, render: input("design_article", "Артикул КД") },
    { key: "calculated_name", title: "Наименование (Расчет)", value: (line) => drafts[line.id]?.calculated_name, render: input("calculated_name", "Наименование (Расчет)") },
    { key: "site_name", title: "Наименование (Сайт)", value: (line) => drafts[line.id]?.site_name, render: input("site_name", "Наименование (Сайт)") },
    { key: "link", title: "Ссылка", value: (line) => drafts[line.id]?.link, render: input("link", "Ссылка") },
    { key: "supplier", title: "Поставщик", value: (line) => drafts[line.id]?.supplier, render: input("supplier", "Поставщик") },
    { key: "calculated_quantity", title: "Кол-во (Расчет)", value: (line) => line.calculated_quantity, numeric: true },
    { key: "calculated_unit", title: "Ед.изм (Расчет)", value: (line) => line.calculated_unit },
    { key: "actual_quantity", title: "Кол-во (факт)", value: (line) => drafts[line.id]?.actual_quantity, render: input("actual_quantity", "Кол-во (факт)", "number") },
    {
      key: "actual_unit", title: "Ед.изм (факт)", value: (line) => drafts[line.id]?.actual_unit,
      render: (line) => {
        const value = drafts[line.id]?.actual_unit ?? "";
        return (
          <select aria-label={`Ед.изм (факт) ${line.legacy_id}`} value={value} onChange={(event) => edit(line, "actual_unit", event.target.value)}>
            <option value="">—</option>
            {units.map((unit) => <option key={unit} value={unit}>{unit}</option>)}
            {value && !units.includes(value) && <option value={value}>{value} (в таблице)</option>}
          </select>
        );
      },
    },
  ];

  return (
    <section className="panel table-panel">
      <div className="panel-heading"><h2>Заполнить данные</h2><button type="button" className="secondary small" onClick={onClose}>Закрыть</button></div>
      <p className="hint">Строки «В работе». Сохраняются только строки с галочкой «В обработку» — она ставится сама, как только начнёшь заполнять строку. Поставщик обязателен: отмеченная строка без поставщика не сохранится. Новый поставщик в пустой ячейке запомнится для домена ссылки. Кол-во и ед.изм. (факт) нужны для запроса поставщику.</p>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <DataTable
        label="Заполнить данные"
        rows={lines}
        columns={columns}
        rowKey={(line) => line.id}
        emptyText={loading ? "Загружаю…" : "У тебя нет строк со статусом «В работе»."}
      />
      <div className="card-footer">
        <small>Отмечено: {marked.size}</small>
        <button type="button" className="primary small" disabled={saving || !marked.size} onClick={() => void save()}>
          {saving ? "Сохраняем…" : marked.size ? `Сохранить отмеченные (${marked.size})` : "Сохранить отмеченные"}
        </button>
      </div>
    </section>
  );
}
