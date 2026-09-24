import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  requestsApi,
  type Principal,
  type RequestRow,
  type RequestSchema,
} from "./api";

type Draft = {
  purpose: string;
  project_reference: string;
  delivery_point: string;
  name: string;
  item_type: string;
  quantity: string;
  unit: string;
  category: string;
  quality_document: string;
  procurement_deadline: string;
  planned_date: string;
  comment: string;
};

function dateAfter(days: number) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function emptyDraft(): Draft {
  return {
    purpose: "RND",
    project_reference: "",
    delivery_point: "",
    name: "",
    item_type: "Комплектующие",
    quantity: "1",
    unit: "шт",
    category: "Общепром",
    quality_document: "Нет",
    procurement_deadline: dateAfter(7),
    planned_date: dateAfter(14),
    comment: "",
  };
}

export function RequestsPage({ principal }: { principal: Principal }) {
  const creationKey = useRef(crypto.randomUUID());
  const [schema, setSchema] = useState<RequestSchema | null>(null);
  const [rows, setRows] = useState<RequestRow[]>([]);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const canEdit = principal.permissions.requests === "edit";
  const canHandoff =
    principal.permissions.procurement === "edit" &&
    principal.roles.some((role) => role === "buyer" || role === "admin");

  async function reload() {
    const [nextSchema, result] = await Promise.all([requestsApi.schema(), requestsApi.list()]);
    setSchema(nextSchema);
    setRows(result.requests);
    setDraft((current) => ({
      ...current,
      purpose: current.purpose || nextSchema.purposes[0] || "",
      delivery_point: current.delivery_point || nextSchema.delivery_points[0] || "",
    }));
  }

  useEffect(() => {
    reload().catch((reason) => setError(reason instanceof Error ? reason.message : "Ошибка загрузки"));
  }, []);

  function field<K extends keyof Draft>(name: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [name]: value }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const payload = Object.fromEntries(
        Object.entries(draft).filter(([, value]) => value !== ""),
      );
      const result = await requestsApi.create(payload, creationKey.current);
      creationKey.current = crypto.randomUUID();
      setNotice(`Создана заявка ${result.requests[0].legacy_id}`);
      setDraft((current) => ({ ...emptyDraft(), delivery_point: current.delivery_point }));
      try {
        await reload();
      } catch (reason) {
        setError(
          reason instanceof Error
            ? `Заявка создана, но список не обновился: ${reason.message}`
            : "Заявка создана, но список не обновился",
        );
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось создать заявку");
    } finally {
      setBusy(false);
    }
  }

  async function handoff(id: string) {
    setBusy(true);
    setError("");
    try {
      await requestsApi.handoff([id]);
      setNotice("Заявка передана в очередь закупки");
      await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось передать заявку");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div><span className="eyebrow">Входящий поток</span><h1>Заявки</h1><p>Создание потребности и передача закупщику.</p></div>
        <span className="counter">{rows.length} строк</span>
      </header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}
      <div className="split-layout">
        {canEdit ? <form className="panel request-form" onSubmit={submit}>
          <div className="panel-heading"><h2>Новая заявка</h2><span>Обязательные поля отмечены *</span></div>
          <div className="form-grid">
            <label>Назначение *<select value={draft.purpose} onChange={(e) => field("purpose", e.target.value)}>{schema?.purposes.map((item) => <option key={item}>{item}</option>)}</select></label>
            <label>Проект<input value={draft.project_reference} onChange={(e) => field("project_reference", e.target.value)} placeholder="Например, 591К.3" /></label>
            <label className="wide">Точка отгрузки *<select value={draft.delivery_point} onChange={(e) => field("delivery_point", e.target.value)} required><option value="">Выберите точку</option>{schema?.delivery_points.map((item) => <option key={item}>{item}</option>)}</select></label>
            <label className="wide">Наименование *<input value={draft.name} onChange={(e) => field("name", e.target.value)} required /></label>
            <label>Тип *<input value={draft.item_type} onChange={(e) => field("item_type", e.target.value)} required /></label>
            <label>Категория<select value={draft.category} onChange={(e) => field("category", e.target.value)}>{schema?.categories.map((item) => <option key={item}>{item}</option>)}</select></label>
            <label>Количество *<input type="number" min="0.000001" step="any" value={draft.quantity} onChange={(e) => field("quantity", e.target.value)} required /></label>
            <label>Единица *<select value={draft.unit} onChange={(e) => field("unit", e.target.value)}>{schema?.units.map((item) => <option key={item}>{item}</option>)}</select></label>
            <label>Документ качества *<select value={draft.quality_document} onChange={(e) => field("quality_document", e.target.value)}><option>Нет</option><option>Да</option></select></label>
            <label>Срок закупки *<input type="date" value={draft.procurement_deadline} onChange={(e) => field("procurement_deadline", e.target.value)} required /></label>
            <label>Плановая дата *<input type="date" value={draft.planned_date} onChange={(e) => field("planned_date", e.target.value)} required /></label>
            <label className="wide">Комментарий<textarea value={draft.comment} onChange={(e) => field("comment", e.target.value)} rows={3} /></label>
          </div>
          <button className="primary" disabled={busy || !schema}>{busy ? "Сохраняем…" : "Создать заявку"}</button>
        </form> : <div className="panel readonly-panel"><h2>Только просмотр</h2><p>У вас нет права создавать заявки.</p></div>}
        <div className="panel table-panel">
          <div className="panel-heading"><h2>Последние заявки</h2><span>До 200 строк</span></div>
          <div className="table-scroll">
            <table><thead><tr><th>ID</th><th>Наименование</th><th>Назначение</th><th>Кол-во</th><th>Приоритет</th><th>Статус</th><th /></tr></thead>
              <tbody>{rows.map((row) => <tr key={row.id}><td><strong>{row.legacy_id}</strong><small>{row.designation}</small></td><td>{row.name}<small>{row.item_type}</small></td><td>{row.project_reference || row.purpose}</td><td>{row.quantity} {row.unit}</td><td>{row.priority}</td><td><span className="status">{row.status}</span></td><td>{row.status === "Новый" && canHandoff && <button className="secondary small" disabled={busy} onClick={() => handoff(row.id)}>В закупку</button>}</td></tr>)}</tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}
