import { useEffect, useState } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { apiTokensApi, failureText, type ApiTokenRow, type ApiTokenStatus } from "./api";
import { formatDate, formatDay } from "./format";

/** «Токены API» в «Доступах»: токен даёт программе (генератору техкарт, загрузчику схем сборки) вход без пароля.
 *  Секрет виден один раз — сразу после выпуска; в состоянии страницы он живёт, только пока открыта панель. */

const SCOPES: Array<[key: string, label: string]> = [
  ["tcgen", "Генератор техкарт"],
  ["assembly", "Черновики сборки"],
  ["refs:read", "Чтение базы данных"],
  ["refs:write", "Загрузка в базу данных"],
  ["labels:print", "Печать этикеток (агент склада)"],
];
const SCOPE_LABEL: Record<string, string> = Object.fromEntries(SCOPES);
const STATUS: Record<ApiTokenStatus, { label: string; tone: string }> = {
  active: { label: "действует", tone: "tone-ok" },
  expired: { label: "истёк", tone: "tone-warn" },
  revoked: { label: "отозван", tone: "tone-danger" },
};
const DEFAULT_DAYS = 90;

export function ApiTokens() {
  const [tokens, setTokens] = useState<ApiTokenRow[] | null>(null);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>([]);
  const [days, setDays] = useState(String(DEFAULT_DAYS));
  const [secret, setSecret] = useState<{ name: string; token: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = async () => setTokens((await apiTokensApi.list()).tokens);

  useEffect(() => {
    load().catch((reason) => setError(failureText(reason, "Не удалось загрузить токены")));
  }, []);

  async function issue() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const issued = await apiTokensApi.issue({ name: name.trim(), scopes, days: Number(days) });
      setSecret({ name: issued.name, token: issued.token });
      setCopied(false);
      setName("");
      setScopes([]);
      setDays(String(DEFAULT_DAYS));
      await load();
    } catch (reason) {
      setError(failureText(reason, "Не удалось выпустить токен"));
    } finally {
      setBusy(false);
    }
  }

  async function revoke(token: ApiTokenRow) {
    if (busy || !window.confirm(`Отозвать токен «${token.name}»? Программа с этим токеном перестанет заходить.`)) return;
    setBusy(true);
    setError("");
    try {
      await apiTokensApi.revoke(token.id);
      setSecret(null);
      await load();
    } catch (reason) {
      setError(failureText(reason, "Не удалось отозвать токен"));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!secret) return;
    try {
      await navigator.clipboard.writeText(secret.token);
      setCopied(true);
    } catch {
      setError("Не удалось скопировать — выделите токен и скопируйте вручную.");
    }
  }

  const statusOf = (token: ApiTokenRow) => STATUS[token.status] ?? { label: token.status, tone: "tone-neutral" };
  const columns: DataColumn<ApiTokenRow>[] = [
    { key: "name", title: "Название", value: (token) => token.name, wrap: true },
    { key: "scopes", title: "Области", value: (token) => token.scopes.map((scope) => SCOPE_LABEL[scope] ?? scope).join(", ") },
    { key: "created_by", title: "Выпустил", value: (token) => token.created_by },
    { key: "created_at", title: "Создан", value: (token) => formatDay(token.created_at) },
    { key: "expires_at", title: "Действует до", value: (token) => formatDay(token.expires_at) },
    { key: "last_used_at", title: "Последнее использование", value: (token) => token.last_used_at ? formatDate(token.last_used_at) : "не использовался" },
    {
      key: "status", title: "Статус", filter: true, value: (token) => statusOf(token).label,
      render: (token) => <span className={`access-pill access-set ${statusOf(token).tone}`}>{statusOf(token).label}</span>,
    },
    {
      key: "actions", title: "", value: () => "",
      render: (token) => token.status === "revoked" ? null
        : <button className="secondary small token-revoke" disabled={busy} onClick={() => void revoke(token)}>Отозвать</button>,
    },
  ];

  const daysNumber = Number(days);
  const canIssue = name.trim() !== "" && scopes.length > 0 && Number.isInteger(daysNumber) && daysNumber >= 1 && daysNumber <= 365;

  return <article className="api-tokens">
    {error && <div className="error-banner" role="alert">{error}</div>}
    {secret && <div className="panel token-secret" role="region" aria-label="Новый токен">
      <p><strong>Токен «{secret.name}» выпущен.</strong> Токен показывается один раз — сохраните его сейчас.</p>
      <div className="token-secret-row">
        <input readOnly className="token-secret-value" aria-label="Токен" value={secret.token} onFocus={(event) => event.currentTarget.select()} />
        <button className="primary small" onClick={() => void copy()}>{copied ? "Скопировано" : "Скопировать"}</button>
        <button className="secondary small" onClick={() => setSecret(null)}>Закрыть</button>
      </div>
    </div>}
    <section className="panel token-issue" aria-label="Выпустить токен">
    <h3>Выпустить токен</h3>
    <form className="token-form" onSubmit={(event) => { event.preventDefault(); void issue(); }}>
      <label>Название
        <input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} />
      </label>
      <fieldset className="check-field" aria-label="Области">
        {SCOPES.map(([key, label]) => <label key={key} className="check-inline">
          <input type="checkbox" checked={scopes.includes(key)}
            onChange={(event) => setScopes(event.target.checked ? [...scopes, key] : scopes.filter((scope) => scope !== key))} />
          {label}
        </label>)}
      </fieldset>
      <label>Срок, дней
        <input type="number" min={1} max={365} value={days} onChange={(event) => setDays(event.target.value)} />
      </label>
      <button className="primary small token-submit" type="submit" disabled={busy || !canIssue}>Выпустить токен</button>
    </form>
    {!canIssue && !busy && <p className="token-hint">{name.trim() === "" || scopes.length === 0 ? "Укажите название и хотя бы одну область" : "Срок — целое число дней от 1 до 365"}</p>}
    </section>
    <h3 className="token-list-title">Выданные токены</h3>
    {tokens === null && !error ? <p className="empty">Загрузка…</p>
      : <DataTable label="Токены API" rows={tokens ?? []} rowKey={(token) => token.id} columns={columns}
        emptyText="Токенов нет — выпустите первый" />}
  </article>;
}
