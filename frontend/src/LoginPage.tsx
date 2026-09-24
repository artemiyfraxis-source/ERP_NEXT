import { useState, type FormEvent } from "react";
import { identityApi, type Principal } from "./api";

export function LoginPage({ onLogin }: { onLogin: (principal: Principal) => void }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(await identityApi.login(login, password));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось войти");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div className="logo-row"><strong className="wordmark">FRAXIS</strong><span className="div" /><span className="mod">ERP</span></div>
        <h1>Вход в систему</h1>
        <p className="sub">Управление производством Fraxis</p>
        <label>Логин<input autoComplete="username" value={login} onChange={(e) => setLogin(e.target.value)} required /></label>
        <label>Пароль<input autoComplete="current-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
        {error && <div className="error-banner" role="alert">{error}</div>}
        <button className="primary" disabled={busy}>{busy ? "Входим…" : "Войти"}</button>
      </form>
    </main>
  );
}
