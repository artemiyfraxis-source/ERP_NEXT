import { useCallback, useEffect, useMemo, useState } from "react";
import { urlParam } from "./navigation";
import { DataTable, type DataColumn } from "./DataTable";
import { failureText } from "./api";
import { formatDay } from "./format";
import {
  SUPPLIER_CATEGORIES, purchasingDirectoryApi, supplierFormError,
  type ImportPlan, type Supplier, type SupplierCard, type SupplierFilters, type SupplierInput,
} from "./purchasingDirectoryApi";

const EMPTY: SupplierInput = {
  name: "", inn: null, categories: [], is_key: false, contact_person: null, phone: null, email: null, site: null,
  domains: [], notes: null,
};

const money = (value: string | null | undefined) =>
  value === null || value === undefined ? "—" : Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 });

function inputOf(supplier: Supplier): SupplierInput {
  const { name, inn, categories, is_key, contact_person, phone, email, site, domains, notes } = supplier;
  return { name, inn, categories, is_key, contact_person, phone, email, site, domains, notes };
}

function SupplierForm({ initial, submitLabel, busy, onSubmit, onCancel }: {
  initial: SupplierInput; submitLabel: string; busy: boolean;
  onSubmit: (input: SupplierInput) => void; onCancel?: () => void;
}) {
  const [form, setForm] = useState(initial);
  const [domains, setDomains] = useState(initial.domains.join(", "));
  const [problem, setProblem] = useState("");
  const text = (key: "name" | "inn" | "contact_person" | "phone" | "email" | "site" | "notes") => ({
    value: form[key] ?? "",
    onChange: (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value }),
  });

  function submit() {
    const input: SupplierInput = {
      ...form,
      domains: domains.split(/[\s,;]+/).filter(Boolean),
      inn: (form.inn ?? "").trim() || null,
    };
    const message = supplierFormError(input);
    setProblem(message ?? "");
    if (!message) onSubmit(input);
  }

  return (
    <div className="dialog-form">
      {problem && <div className="error-banner" role="alert">{problem}</div>}
      <div className="move-grid two">
        <label className="wide">Название<input {...text("name")} /></label>
        <label>ИНН<input {...text("inn")} inputMode="numeric" /></label>
        <label>Контакт<input {...text("contact_person")} /></label>
        <label>Телефон<input {...text("phone")} /></label>
        <label>Почта<input {...text("email")} /></label>
        <label>Сайт<input {...text("site")} /></label>
        <label>Домены (через запятую)<input value={domains} onChange={(event) => setDomains(event.target.value)} /></label>
      </div>
      <div className="supplier-categories" role="group" aria-label="Категории">
        {Object.entries(SUPPLIER_CATEGORIES).map(([key, label]) => (
          <label key={key} className="inline-check">
            <input
              type="checkbox"
              checked={form.categories.includes(key)}
              onChange={(event) => setForm({
                ...form,
                categories: event.target.checked ? [...form.categories, key] : form.categories.filter((c) => c !== key),
              })}
            />
            {label}
          </label>
        ))}
        <label className="inline-check">
          <input type="checkbox" checked={form.is_key} onChange={(event) => setForm({ ...form, is_key: event.target.checked })} />
          Ключевой
        </label>
      </div>
      <label>Заметки<textarea rows={2} {...text("notes")} /></label>
      <div className="move-actions">
        {onCancel && <button className="secondary" onClick={onCancel}>Отмена</button>}
        <button className="primary" disabled={busy} onClick={submit}>{submitLabel}</button>
      </div>
    </div>
  );
}

function ImportDialog({ plan, busy, error, onApply, onClose }: { plan: ImportPlan; busy: boolean; error: string; onApply: () => void; onClose: () => void }) {
  const attached = plan.existing.filter((item) => item.new_variants.length || item.new_domains.length);
  return (
    <div className="dialog-overlay" role="dialog" aria-label="Собрать из закупок">
      <div className="panel move-dialog wide">
        <h2>Собрать поставщиков из закупок</h2>
        {error && <div className="error-banner" role="alert">{error}</div>}
        <p className="page-sub">
          Новых: {plan.new.length}; уже в справочнике: {plan.existing.length}
          {attached.length ? ` (дополнятся: ${attached.length})` : ""}.
        </p>
        {plan.new.length > 0 && (
          <div className="history-table">
            <table>
              <thead><tr><th>Новый поставщик</th><th>Строк</th><th>Варианты написания</th><th>Домены</th></tr></thead>
              <tbody>
                {plan.new.map((item) => (
                  <tr key={item.key}>
                    <td>{item.name}</td>
                    <td>{item.lines}</td>
                    <td>{item.variants.map((v) => `${v.text} (${v.lines})`).join("; ")}</td>
                    <td>{item.domains.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {attached.length > 0 && (
          <div className="history-table">
            <table>
              <thead><tr><th>Уже есть — дополнится</th><th>Новые варианты</th><th>Новые домены</th></tr></thead>
              <tbody>
                {attached.map((item) => (
                  <tr key={item.supplier_id}>
                    <td>{item.name}</td>
                    <td>{item.new_variants.map((v) => v.text).join("; ")}</td>
                    <td>{item.new_domains.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="move-actions">
          <button className="secondary" onClick={onClose}>Закрыть</button>
          <button className="primary" disabled={busy || (plan.new.length === 0 && attached.length === 0)} onClick={onApply}>
            Создать {plan.new.length}
          </button>
        </div>
      </div>
    </div>
  );
}

function CardPanel({ card, readOnly, busy, onSave, onArchive, onClose }: {
  card: SupplierCard; readOnly: boolean; busy: boolean;
  onSave: (input: SupplierInput) => void; onArchive: () => void; onClose: () => void;
}) {
  const stats = card.procurement;
  const stages = Object.entries(stats.open_by_stage);
  return (
    <aside className="panel supplier-card" aria-label="Карточка поставщика">
      <div className="panel-heading">
        <h2>{card.name}</h2>
        <button className="link-button" onClick={onClose}>Закрыть</button>
      </div>
      <div className="supplier-card-body">
        {readOnly ? (
          <p className="page-sub">{[card.inn && `ИНН ${card.inn}`, card.phone, card.email, card.domains.join(", ")].filter(Boolean).join(" · ") || "Контакты не заполнены"}</p>
        ) : (
          <SupplierForm key={`${card.id}:${card.version}`} initial={inputOf(card)} submitLabel="Сохранить" busy={busy} onSubmit={onSave} />
        )}
        <h3>Закупки</h3>
        <ul className="supplier-stats">
          <li>Строк закупки: <strong>{stats.lines_total}</strong></li>
          <li>Оплачено строк: <strong>{stats.paid_lines}</strong> на <strong>{money(stats.paid_sum)}</strong> ₽</li>
          <li>Поставки в срок: <strong>{stats.on_time_pct === null ? "—" : `${stats.on_time_pct}%`}</strong> (с датами: {stats.received_with_date})</li>
          <li>В работе: {stages.length ? stages.map(([stage, count]) => `${stage} — ${count}`).join("; ") : "нет"}</li>
        </ul>
        <h3>Запросы КП</h3>
        <ul className="supplier-stats">
          <li>Отправлено запросов: <strong>{card.rfq.sent}</strong></li>
          <li>Ответили: <strong>{card.rfq.answered_pct === null ? "—" : `${card.rfq.answered_pct}%`}</strong>; КП: {card.rfq.quotes}</li>
        </ul>
        <h3>Последние закупки</h3>
        {stats.last_purchases.length === 0 ? <p className="muted">Закупок по этому поставщику пока нет.</p> : (
          <div className="history-table">
            <table>
              <thead><tr><th>Дата</th><th>Позиция</th><th>Кол-во</th><th>Цена с НДС</th><th>Заказ</th></tr></thead>
              <tbody>
                {stats.last_purchases.map((p, index) => (
                  <tr key={`${p.order_number}-${index}`}>
                    <td>{formatDay(p.date)}</td>
                    <td>{p.name}{p.article ? <small>{p.article}</small> : null}</td>
                    <td>{p.quantity === null ? "—" : `${money(p.quantity)} ${p.unit ?? ""}`}</td>
                    <td>{money(p.unit_price_with_vat)}</td>
                    <td>{p.order_number ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!readOnly && (
          <div className="move-actions">
            <button className="secondary" disabled={busy} onClick={onArchive}>{card.archived ? "Вернуть из архива" : "В архив"}</button>
          </div>
        )}
      </div>
    </aside>
  );
}

/** «Справочник поставщиков» — карточки поставщиков с ИНН, категориями, доменами и статистикой по закупкам. */
export function PurchasingSuppliers({ readOnly = false }: { readOnly?: boolean } = {}) {
  const [filters, setFilters] = useState<SupplierFilters>({ q: urlParam("q"), category: "", key: false, archived: false });
  const [rows, setRows] = useState<Supplier[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [card, setCard] = useState<SupplierCard | null>(null);
  const [plan, setPlan] = useState<ImportPlan | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await purchasingDirectoryApi.suppliers(filters));
      setError("");
    } catch (reason) {
      setError(failureText(reason, "Не удалось загрузить поставщиков"));
    }
  }, [filters]);

  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, filters.q ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [load, filters.q]);

  async function run(action: () => Promise<unknown>, fallback: string) {
    setBusy(true);
    setError("");
    try {
      await action();
      return true;
    } catch (reason) {
      setError(failureText(reason, fallback));
      return false;
    } finally {
      setBusy(false);
    }
  }

  const open = (id: string) => run(async () => setCard(await purchasingDirectoryApi.card(id)), "Не удалось открыть карточку");

  async function create(input: SupplierInput) {
    const ok = await run(async () => {
      const made = await purchasingDirectoryApi.create(input);
      setCreating(false);
      await load();
      setCard(await purchasingDirectoryApi.card(made.id));
    }, "Не удалось создать поставщика");
    return ok;
  }

  const columns = useMemo<DataColumn<Supplier>[]>(() => [
    { key: "name", title: "Название", value: (s) => s.name },
    { key: "inn", title: "ИНН", value: (s) => s.inn },
    { key: "categories", title: "Категории", value: (s) => s.categories.map((c) => SUPPLIER_CATEGORIES[c] ?? c).join(", ") },
    { key: "key", title: "Ключевой", value: (s) => (s.is_key ? "★" : ""), render: (s) => (s.is_key ? <span title="Ключевой поставщик">★</span> : null) },
    { key: "contact", title: "Контакт", value: (s) => s.contact_person },
    { key: "phone", title: "Телефон / почта", value: (s) => [s.phone, s.email].filter(Boolean).join(" · ") },
    { key: "domains", title: "Домены", value: (s) => s.domains.join(", ") },
    { key: "count", title: "Закупок", numeric: true, value: (s) => s.purchases_count ?? 0 },
    { key: "sum", title: "Сумма, ₽", numeric: true, value: (s) => Number(s.purchases_sum ?? 0), render: (s) => money(s.purchases_sum) },
  ], []);

  return (
    <section className="page">
      <div className="page-head">
        <h1>Поставщики</h1>
        {rows && <span className="page-count">{rows.length}</span>}
        {!readOnly && (
          <div className="page-actions">
            <button className="secondary" disabled={busy} onClick={() => run(async () => setPlan(await purchasingDirectoryApi.importFromProcurement(true)), "Не удалось собрать поставщиков")}>
              Собрать из закупок
            </button>
            <button className="primary" onClick={() => setCreating(true)}>+ Поставщик</button>
          </div>
        )}
      </div>
      <div className="toolbar supplier-toolbar">
        <input
          type="search"
          aria-label="Поиск поставщика"
          placeholder="Название, ИНН, домен"
          value={filters.q}
          onChange={(event) => setFilters({ ...filters, q: event.target.value })}
        />
        <select aria-label="Категория" value={filters.category} onChange={(event) => setFilters({ ...filters, category: event.target.value })}>
          <option value="">Все категории</option>
          {Object.entries(SUPPLIER_CATEGORIES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
        <label className="inline-check">
          <input type="checkbox" checked={filters.key} onChange={(event) => setFilters({ ...filters, key: event.target.checked })} />
          Ключевые
        </label>
        <label className="inline-check">
          <input type="checkbox" checked={filters.archived} onChange={(event) => setFilters({ ...filters, archived: event.target.checked })} />
          Архив
        </label>
      </div>
      {error && !creating && !plan && <div className="error-banner" role="alert">{error}</div>}
      <div className={card ? "supplier-layout with-card" : "supplier-layout"}>
        {rows && (
          <DataTable
            rows={rows}
            columns={columns}
            rowKey={(s) => s.id}
            compactBelow={Number.MAX_SAFE_INTEGER}
            onRowClick={(s) => { void open(s.id); }}
            rowClassName={(s) => (card?.id === s.id ? "open" : "")}
            emptyText="Поставщиков нет — добавьте или соберите из закупок"
          />
        )}
        {card && (
          <CardPanel
            card={card}
            readOnly={readOnly}
            busy={busy}
            onClose={() => setCard(null)}
            onSave={(input) => {
              void run(async () => {
                await purchasingDirectoryApi.update(card.id, card.version, input);
                await load();
                setCard(await purchasingDirectoryApi.card(card.id));
              }, "Не удалось сохранить поставщика");
            }}
            onArchive={() => {
              void run(async () => {
                await purchasingDirectoryApi.archive(card.id, !card.archived);
                setCard(null);
                await load();
              }, "Не удалось изменить архив");
            }}
          />
        )}
      </div>
      {creating && (
        <div className="dialog-overlay" role="dialog" aria-label="Новый поставщик">
          <div className="panel move-dialog">
            <h2>Новый поставщик</h2>
            {error && <div className="error-banner" role="alert">{error}</div>}
            <SupplierForm initial={EMPTY} submitLabel="Создать" busy={busy} onSubmit={(input) => { void create(input); }} onCancel={() => setCreating(false)} />
          </div>
        </div>
      )}
      {plan && (
        <ImportDialog
          plan={plan}
          busy={busy}
          error={error}
          onClose={() => setPlan(null)}
          onApply={() => {
            void run(async () => {
              await purchasingDirectoryApi.importFromProcurement(false);
              setPlan(null);
              await load();
            }, "Не удалось создать поставщиков");
          }}
        />
      )}
    </section>
  );
}
