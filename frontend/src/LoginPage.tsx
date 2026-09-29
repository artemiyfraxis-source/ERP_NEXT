import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { identityApi, type LoginStaff, type PinReveal, type Principal } from "./api";

/** Вход (SUPPLYMAP PortalApp.html renderLogin / showPinCard_, Portal.gs portalLogin): the employee finds themself in
 *  the list and types the PIN; an empty PIN on the first sign-in shows their PIN once («узнать PIN»). The login +
 *  password form stays next to it for accounts without a PIN. */

const WHO_KEY = "erp.login.who"; // the last choice, remembered locally (SUPPLYMAP WHO_KEY)

function rememberedWho(): string {
  try {
    return window.localStorage.getItem(WHO_KEY) ?? "";
  } catch {
    return "";
  }
}

function remember(who: string) {
  try {
    window.localStorage.setItem(WHO_KEY, who);
  } catch {
    /* private window — nothing to remember */
  }
}

const errorText = (reason: unknown) => (reason instanceof Error ? reason.message : "Не удалось войти");

export function LoginPage({ onLogin }: { onLogin: (principal: Principal) => void }) {
  const [staff, setStaff] = useState<LoginStaff[] | null>(null);
  const [mode, setMode] = useState<"pin" | "password">("pin");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    identityApi
      .loginStaff()
      .then(({ staff: list }) => setStaff(list))
      .catch(() => setStaff([]));
  }, []);

  // No list (the server is older or nobody is there) — the login + password form only.
  const pinAvailable = staff === null || staff.length > 0;
  const shown = pinAvailable ? mode : "password";

  return (
    <main className="login-wrap">
      <div className="login-card">
        <div className="logo-row"><strong className="wordmark">FRAXIS</strong><span className="div" /><span className="mod">ERP</span></div>
        <h1>Вход в систему</h1>
        <p className="sub">Управление производством Fraxis</p>
        {shown === "pin"
          ? <PinForm staff={staff ?? []} busy={busy} setBusy={setBusy} error={error} setError={setError} onLogin={onLogin} />
          : <PasswordForm busy={busy} setBusy={setBusy} error={error} setError={setError} onLogin={onLogin} />}
        {pinAvailable && (
          <button
            type="button"
            className="link-button login-alt"
            onClick={() => { setError(""); setMode(shown === "pin" ? "password" : "pin"); }}
          >
            {shown === "pin" ? "Войти по логину и паролю" : "Войти по PIN"}
          </button>
        )}
      </div>
    </main>
  );
}

type FormProps = {
  busy: boolean;
  setBusy: (busy: boolean) => void;
  error: string;
  setError: (error: string) => void;
  onLogin: (principal: Principal) => void;
};

function PinForm({ staff, busy, setBusy, error, setError, onLogin }: FormProps & { staff: LoginStaff[] }) {
  const [who, setWho] = useState(() => rememberedWho());
  const [pin, setPin] = useState("");
  const [revealed, setRevealed] = useState<PinReveal | null>(null);
  const pinRef = useRef<HTMLInputElement>(null);

  // «Просто выбор себя и PIN» (owner 2026-09-29): the list alone, sorted by surname; a <select> searches by typing on
  // its own, so no separate search box. Names are «Фамилия Имя», so a plain ru sort is already by surname.
  const options = useMemo(
    () => [...staff].sort((a, b) => a.name.localeCompare(b.name, "ru")),
    [staff],
  );
  const known = staff.some((person) => person.id === who);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!known) {
      setError("Выбери себя в списке.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      // An empty PIN is not cut off here: for a newcomer it asks «покажи мой PIN» — the server decides.
      const result = await identityApi.pinLogin(who, pin);
      if ("reveal" in result) {
        setRevealed(result);
        return;
      }
      remember(who);
      onLogin(result);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  function written() {
    if (!revealed) return;
    setPin(revealed.pin);
    setRevealed(null);
    pinRef.current?.focus();
  }

  return (
    <form onSubmit={submit}>
      <label>Кто ты
        <select aria-label="Сотрудник" value={known ? who : ""} onChange={(event) => { setWho(event.target.value); pinRef.current?.focus(); }}>
          <option value="">— выбери себя —</option>
          {options.map((person) => (
            <option key={person.id} value={person.id}>{person.name}</option>
          ))}
        </select>
      </label>
      <label>PIN-код
        <input
          ref={pinRef}
          aria-label="PIN-код"
          type="password"
          inputMode="numeric"
          autoComplete="current-password"
          maxLength={8}
          placeholder="••••••"
          value={pin}
          onChange={(event) => setPin(event.target.value)}
        />
      </label>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <button className="primary" disabled={busy}>{busy ? "Проверяю…" : "Войти"}</button>
      {revealed && (
        <div className="pin-overlay" role="dialog" aria-label="Ваш PIN">
          <div className="pin-card">
            <h2>{revealed.name}</h2>
            <p>Ваш пароль для входа в портал</p>
            <div className="pin-reveal">{revealed.pin}</div>
            <p className="sub">
              Запишите пароль, чтобы не потерять — второй раз он не покажется. Менять его может администратор в
              разделе «Сотрудники».
            </p>
            <button type="button" className="primary" onClick={written}>Записал</button>
          </div>
        </div>
      )}
    </form>
  );
}

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
