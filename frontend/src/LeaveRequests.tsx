import { useCallback, useEffect, useState } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import {
  failureText,
  leaveApi,
  type LeaveDraft,
  type LeaveKind,
  type LeaveOverview,
  type LeaveRequestView,
  type LeaveStatus,
} from "./api";
import { formatDate } from "./format";

/** «Отпуска и заявления» в «Моей карточке»: свои заявления на отпуск и отгул, входящие на согласование (руководитель,
 *  замещающий, «Кадры») и журнал кадров. Каждый шаг — подпись PIN или паролем; решения после подписи видны в строке. */

const STATUS_TONE: Record<LeaveStatus, string> = {
  draft: "neutral", signed: "info", approved: "info", registered: "ok", rejected: "danger", withdrawn: "neutral",
};

type Props = {
  /** Сколько заявлений ждут решения этого человека — для счётчика на вкладке. */
  onWaiting?: (count: number) => void;
};

export function LeaveRequests({ onWaiting }: Props) {
  const [overview, setOverview] = useState<LeaveOverview | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [form, setForm] = useState<{ item: LeaveRequestView | null } | null>(null);

  const load = useCallback(async () => {
    try {
      const loaded = await leaveApi.overview();
      setOverview(loaded);
      setError("");
      onWaiting?.(loaded.inbox.length);
    } catch (reason) {
      setError(failureText(reason, "Заявления не загружены"));
    }
  }, [onWaiting]);

  useEffect(() => { void load(); }, [load]);

  const done = useCallback(async (text: string) => {
    setMessage(text);
    setForm(null);
    await load();
  }, [load]);

  if (error && !overview) return <div className="error-banner" role="alert">{error}</div>;
  if (!overview) return <div className="empty">Загрузка…</div>;

  const { balance, today } = overview;
  const edit = (item: LeaveRequestView) => { setMessage(""); setForm({ item }); };
  const detail = (item: LeaveRequestView) => <LeaveDetail item={item} onDone={done} onEdit={edit} />;

  return <div className="lv-page">
    {message && <div className="success-banner" role="status">{message}</div>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    <section className="panel lv-summary" aria-label={`Отпуск ${balance.year}`}>
      <h2>Отпуск {balance.year}</h2>
      <dl className="lv-balance">
        <div><dt>Норма</dt><dd>{balance.norm} дн.</dd></div>
        <div><dt>Зарегистрировано</dt><dd>{balance.registered} дн.</dd></div>
        <div><dt>В заявлениях</dt><dd>{balance.pending} дн.</dd></div>
        <div className="lv-left"><dt>Осталось</dt><dd>{balance.left} дн.</dd></div>
      </dl>
      {!form && <button className="primary" onClick={() => { setMessage(""); setForm({ item: null }); }}>Новое заявление</button>}
    </section>

    {form && <LeaveForm key={form.item?.id ?? "new"} item={form.item} today={today} onCancel={() => setForm(null)} onDone={done} />}

    {overview.inbox.length > 0 && <section className="panel lv-block" aria-label="На согласование">
      <h2>На согласование<span className="page-count">{overview.inbox.length}</span></h2>
      <DataTable
        rows={overview.inbox} columns={columns(true)} rowKey={(item) => item.id}
        renderDetail={detail} hideSearch pageSize={50}
      />
    </section>}

    <section className="panel lv-block" aria-label="Мои заявления">
      <h2>Мои заявления{overview.own.length > 0 && <span className="page-count">{overview.own.length}</span>}</h2>
      {overview.own.length > 0
        ? <DataTable rows={overview.own} columns={columns(false)} rowKey={(item) => item.id}
          renderDetail={detail} hideSearch pageSize={50} />
        : <p className="empty">Заявлений нет — нажмите «Новое заявление».</p>}
    </section>

    {overview.journal && <section className="panel lv-block" aria-label="Журнал кадров">
      <h2>Журнал кадров{overview.journal.length > 0 && <span className="page-count">{overview.journal.length}</span>}</h2>
      {overview.journal.length > 0
        ? <DataTable rows={overview.journal} columns={columns(true)} rowKey={(item) => item.id}
          renderDetail={detail} storageKey="leave-journal" exportName="Журнал заявлений" pageSize={100} />
        : <p className="empty">Подписанных заявлений пока нет.</p>}
    </section>}
  </div>;
}

function columns(withEmployee: boolean): DataColumn<LeaveRequestView>[] {
  const result: DataColumn<LeaveRequestView>[] = [
    { key: "kind", title: "Вид", width: 90, value: (item) => item.kind_label, filter: true },
    { key: "period", title: "Период", width: 190, value: (item) => item.from_date, render: (item) => period(item) },
    { key: "days", title: "Дней", width: 60, numeric: true, value: (item) => item.days },
    {
      key: "status", title: "Статус", width: 230, filter: true, value: (item) => item.status_label,
      render: (item) => <span className={`kit-status tone-${STATUS_TONE[item.status]}`}>{item.status_label}</span>,
    },
    { key: "waiting", title: "Ждёт решения", wrap: true, value: (item) => item.waiting_for ?? "" },
    { key: "updated", title: "Изменено", width: 130, value: (item) => item.updated_at, render: (item) => formatDate(item.updated_at) },
  ];
  if (withEmployee) result.unshift({ key: "employee", title: "Сотрудник", width: 200, value: (item) => item.employee_name });
  return result;
}

function period(item: { from_date: string; to_date: string }) {
  return item.from_date === item.to_date ? formatDate(item.from_date) : `${formatDate(item.from_date)} – ${formatDate(item.to_date)}`;
}

function addDays(day: string, count: number) {
  const moment = new Date(`${day}T00:00:00Z`);
  moment.setUTCDate(moment.getUTCDate() + count);
  return moment.toISOString().slice(0, 10);
}

function daysBetween(from: string, to: string) {
  const start = Date.parse(`${from}T00:00:00Z`);
  const finish = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(start) || Number.isNaN(finish) || finish < start) return 0;
  return Math.round((finish - start) / 86_400_000) + 1;
}

function SecretField({ value, onChange, className, label = "PIN или пароль для подписи" }: {
  value: string; onChange: (value: string) => void; className?: string; label?: string;
}) {
  return <label className={className}>{label}
    <input type="password" autoComplete="current-password" aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} />
  </label>;
}

function LeaveForm({ item, today, onCancel, onDone }: {
  item: LeaveRequestView | null;
  today: string;
  onCancel: () => void;
  onDone: (message: string) => Promise<void>;
}) {
  const tomorrow = addDays(today, 1);
  const [kind, setKind] = useState<LeaveKind>(item?.kind ?? "vacation");
  const [fromDate, setFromDate] = useState(item?.from_date ?? tomorrow);
  const [toDate, setToDate] = useState(item?.to_date ?? tomorrow);
  const [comment, setComment] = useState(item?.comment ?? "");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<LeaveRequestView | null>(item);
  const days = daysBetween(fromDate, toDate);
  const invalid = !fromDate || !toDate || days === 0 || fromDate < today;

  async function save(sign: boolean) {
    setBusy(true);
    setError("");
    const draft: LeaveDraft = { kind, from_date: fromDate, to_date: toDate, comment };
    try {
      let current = saved ? await leaveApi.update(saved.id, saved.version, draft) : await leaveApi.create(draft);
      setSaved(current);
      if (sign) {
        if (current.status === "draft") current = await leaveApi.sign(current.id, current.version, secret);
        await onDone(`Заявление подписано и отправлено: ${current.waiting_for ?? "на согласование"}.`);
      } else {
        await onDone("Черновик сохранён — подпишите его, когда будете готовы.");
      }
    } catch (reason) {
      setError(failureText(reason, "Заявление не сохранено"));
    } finally {
      setBusy(false);
    }
  }

  return <section className="panel lv-form" aria-label={item ? "Изменить заявление" : "Новое заявление"}>
    <h2>{item ? "Изменить заявление" : "Новое заявление"}</h2>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {item?.status === "signed" && <p className="warning-inline">Заявление уже подписано: после правки его нужно подписать заново.</p>}
    <div className="form-grid">
      <div className="field-group" role="group" aria-label="Вид заявления"><span>Вид</span>
        <div className="seg-tabs">
          {(["vacation", "day_off"] as const).map((value) => <button type="button" key={value} className={kind === value ? "on" : undefined} aria-pressed={kind === value} onClick={() => setKind(value)}>{value === "vacation" ? "Отпуск" : "Отгул"}</button>)}
        </div>
      </div>
      <label>С<input type="date" aria-label="Начало" min={today} value={fromDate} onChange={(event) => { setFromDate(event.target.value); if (toDate < event.target.value) setToDate(event.target.value); }} /></label>
      <label>По<input type="date" aria-label="Конец" min={fromDate || today} value={toDate} onChange={(event) => setToDate(event.target.value)} /></label>
      <div className="lv-days" aria-live="polite"><span>Календарных дней</span><strong>{days || "—"}</strong></div>
      <label className="half">Комментарий<textarea value={comment} maxLength={1000} placeholder="Например: по графику отпусков" onChange={(event) => setComment(event.target.value)} /></label>
      <SecretField className="half" value={secret} onChange={setSecret} />
    </div>
    <div className="form-actions">
      <button className="primary" disabled={busy || invalid || !secret} onClick={() => void save(true)}>Подписать и отправить</button>
      <button className="secondary" disabled={busy || invalid} onClick={() => void save(false)}>Сохранить черновик</button>
      <button className="link-button" disabled={busy} onClick={onCancel}>Отмена</button>
    </div>
  </section>;
}

function LeaveDetail({ item, onDone, onEdit }: {
  item: LeaveRequestView;
  onDone: (message: string) => Promise<void>;
  onEdit: (item: LeaveRequestView) => void;
}) {
  const [secret, setSecret] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { actions } = item;
  const needsSecret = actions.sign || actions.decide || (actions.withdraw && item.status !== "draft");
  const approveLabel = item.status === "approved" ? "Зарегистрировать" : "Согласовать";

  async function run(action: () => Promise<LeaveRequestView>, done: (result: LeaveRequestView) => string) {
    setBusy(true);
    setError("");
    try {
      const result = await action();
      await onDone(done(result));
    } catch (reason) {
      setError(failureText(reason, "Действие не выполнено"));
      setBusy(false);
    }
  }

  return <div className="lv-detail" onClick={(event) => event.stopPropagation()}>
    <dl className="ec-facts">
      <div className="ec-fact"><dt>Заявление</dt><dd>{item.kind_label} · {period(item)} · {item.days} дн. · {item.employee_name}</dd></div>
      {item.comment && <div className="ec-fact"><dt>Комментарий</dt><dd>{item.comment}</dd></div>}
      <div className="ec-fact"><dt>Версия</dt><dd>{item.content_version} <small className="lv-hash" title={`SHA-256 содержания: ${item.content_sha256}`}>{item.content_sha256.slice(0, 12)}</small></dd></div>
    </dl>
    {item.signatures.length > 0 && <ol className="lv-signatures" aria-label="Подписи">
      {item.signatures.map((signature, index) => <li key={`${signature.step}${signature.content_version}${index}`}>
        <span className="lv-step">{signature.step_label}</span>
        <span>{signature.signer_name} {signature.decision_label} {formatDate(signature.signed_at)}</span>
        <small>{signature.method_label} · версия {signature.content_version}</small>
        {signature.comment && <span className="lv-comment">«{signature.comment}»</span>}
      </li>)}
    </ol>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {(actions.sign || actions.decide || actions.withdraw || actions.edit) && <div className="lv-actions">
      {actions.decide && <label className="lv-reason">Комментарий к решению<input value={comment} maxLength={1000} placeholder="Причина обязательна при отказе" onChange={(event) => setComment(event.target.value)} /></label>}
      {needsSecret && <SecretField value={secret} onChange={setSecret} />}
      {actions.sign && <button className="primary small" disabled={busy || !secret} onClick={() => void run(
        () => leaveApi.sign(item.id, item.version, secret),
        (result) => `Заявление подписано и отправлено: ${result.waiting_for ?? "на согласование"}.`,
      )}>Подписать и отправить</button>}
      {actions.decide && <>
        <button className="primary small" disabled={busy || !secret} onClick={() => void run(
          () => leaveApi.decide(item.id, item.version, true, comment, secret),
          (result) => `${result.employee_name}: ${result.status_label.toLowerCase()}.`,
        )}>{approveLabel}</button>
        <button className="secondary small" disabled={busy || !secret || !comment.trim()} onClick={() => void run(
          () => leaveApi.decide(item.id, item.version, false, comment, secret),
          (result) => `${result.employee_name}: заявление отклонено.`,
        )}>Отклонить</button>
      </>}
      {actions.edit && <button className="secondary small" disabled={busy} onClick={() => onEdit(item)}>Изменить</button>}
      {actions.withdraw && <button className="link-button danger-link" disabled={busy || (item.status !== "draft" && !secret)} onClick={() => void run(
        () => leaveApi.withdraw(item.id, item.version, secret, comment),
        () => "Заявление отозвано.",
      )}>Отозвать</button>}
    </div>}
  </div>;
}
