import { useEffect, useState, type FormEvent } from "react";
import { warehouseApi, type UpdLlmSettings } from "./api";
import { PageWindow } from "./PageWindow";

/* «Склад» → «Ещё» → «Распознавание УПД» (owner 26.10.2026, administrator only): the model API that reads paper УПД
   scans. The key is write-only: the field stays empty, «задан: sk-ib…AgnD» says which one is set. */

type Fields = { base_url: string; model: string; fallback_model: string; effort: string };
const ENV_NOTE = "задано на сервере";

export function UpdRecognitionWindow({ close }: { close: () => void }) {
  const [settings, setSettings] = useState<UpdLlmSettings | null>(null);
  const [fields, setFields] = useState<Fields>({ base_url: "", model: "", fallback_model: "", effort: "" });
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  function show(next: UpdLlmSettings) {
    setSettings(next);
    setFields({ base_url: next.base_url, model: next.model, fallback_model: next.fallback_model, effort: next.effort });
    setApiKey("");
  }

  useEffect(() => {
    warehouseApi.updLlmSettings().then(show).catch((reason) => setError(reason instanceof Error ? reason.message : "Не удалось загрузить настройки"));
  }, []);

  async function save(event: FormEvent, clearKey = false) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setSaved("");
    try {
      show(await warehouseApi.saveUpdLlmSettings({ ...fields, ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}), ...(clearKey ? { clear_key: true } : {}) }));
      setSaved(clearKey ? "Ключ удалён" : "Сохранено");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  }

  const fromEnv = (name: string) => Boolean(settings?.env.includes(name));
  const field = (name: keyof Fields, label: string) => (
    <label>{label}
      <input value={fields[name]} disabled={busy || fromEnv(name)} placeholder={fromEnv(name) ? ENV_NOTE : settings?.defaults[name] ?? ""}
        onChange={(event) => setFields({ ...fields, [name]: event.target.value })} />
    </label>
  );
  const keyState = settings ? (fromEnv("api_key") ? `${ENV_NOTE}, ${settings.key_hint}` : settings.key_set ? `задан: ${settings.key_hint}` : "не задан") : "";

  return <PageWindow label="Распознавание УПД" width={720} close={close}>
    <form className="panel" onSubmit={(event) => void save(event)}>
      <header className="page-head"><h2>Распознавание УПД</h2></header>
      {settings === null && !error ? <p className="empty">Загрузка…</p> : null}
      {settings ? <div className="form-grid">
        <label className="half">Адрес API
          <input value={fields.base_url} disabled={busy || fromEnv("base_url")} placeholder={fromEnv("base_url") ? ENV_NOTE : settings.defaults.base_url}
            onChange={(event) => setFields({ ...fields, base_url: event.target.value })} />
        </label>
        {field("model", "Модель")}
        {field("fallback_model", "Запасная модель")}
        <label>Усилие
          <select value={fields.effort} disabled={busy || fromEnv("effort")} onChange={(event) => setFields({ ...fields, effort: event.target.value })}>
            <option value="">по умолчанию ({settings.defaults.effort})</option>
            {settings.efforts.map((effort) => <option key={effort} value={effort}>{effort}</option>)}
          </select>
        </label>
        <label className="half">Ключ API ({keyState})
          <input type="password" autoComplete="off" value={apiKey} disabled={busy || fromEnv("api_key")} placeholder={settings.key_set ? "оставьте пустым, чтобы не менять" : "вставьте ключ"}
            onChange={(event) => setApiKey(event.target.value)} />
        </label>
      </div> : null}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {saved && <p className="success-banner" role="status">{saved}</p>}
      <div className="page-actions">
        <button type="submit" className="primary" disabled={busy || !settings}>Сохранить</button>
        {settings?.key_set && !fromEnv("api_key") ? <button type="button" className="secondary" disabled={busy} onClick={(event) => void save(event, true)}>Удалить ключ</button> : null}
        <button type="button" className="secondary" onClick={close}>Закрыть</button>
      </div>
    </form>
  </PageWindow>;
}
