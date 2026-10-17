import { useState, type FormEvent } from "react";
import { identityApi, type Principal } from "./api";

/** Вход по логину и паролю (owner 01.10.2026: «давай на логины перейдём» — the «choose yourself + PIN» list of #100 is
 *  gone). Logins and passwords are issued by `python -m app.modules.identity.issue_logins`; the e-mail works as a login
 *  too. The PIN endpoints stay on the server, the screen no longer offers them. */

const errorText = (reason: unknown) => (reason instanceof Error ? reason.message : "Не удалось войти");

export function LoginPage({ onLogin }: { onLogin: (principal: Principal) => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <main className="login-wrap">
      <div className="login-card">
        <div className="logo-row"><strong className="wordmark">FRAXIS</strong><span className="div" /><span className="mod">ERP</span></div>
        <h1>Вход в систему</h1>
        <p className="sub">Управление производством Fraxis</p>
        <PasswordForm busy={busy} setBusy={setBusy} error={error} setError={setError} onLogin={onLogin} />
      </div>
    </main>
  );
}

/** Фоновый 401 при открытой странице (аудит F07): вход поверх неё, страница с введёнными данными остаётся под
 *  непрозрачной подложкой — следующему за ПК её не видно, пока он не войдёт; другой сотрудник получает свой экран. */
export function SessionExpiredDialog({ onLogin }: { onLogin: (principal: Principal) => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <div className="login-wrap relogin-backdrop">
      <div className="login-card" role="dialog" aria-modal="true" aria-labelledby="relogin-title">
        <div className="logo-row"><strong className="wordmark">FRAXIS</strong><span className="div" /><span className="mod">ERP</span></div>
        <h1 id="relogin-title">Сессия истекла</h1>
        <p className="sub">Войдите снова — введённое на экране сохранится</p>
        <PasswordForm busy={busy} setBusy={setBusy} error={error} setError={setError} onLogin={onLogin} />
      </div>
    </div>
  );
}

type FormProps = {
  busy: boolean;
  setBusy: (busy: boolean) => void;
  error: string;
  setError: (error: string) => void;
  onLogin: (principal: Principal) => void;
};

function PasswordForm({ busy, setBusy, error, setError, onLogin }: FormProps) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(await identityApi.login(login, password));
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      <label>Логин<input autoComplete="username" value={login} onChange={(e) => setLogin(e.target.value)} required /></label>
      <label>Пароль<input autoComplete="current-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <button className="primary" disabled={busy}>{busy ? "Входим…" : "Войти"}</button>
    </form>
  );
}
