import { useEffect, useState, type FormEvent } from "react";
import { failureText, identityApi, staffApi, type ApprovalPinState } from "./api";
import { formatDate } from "./format";

/** Подвал меню: «Моя карточка». Смена пароля переехала в саму карточку (владелец 14.10) — `ChangePassword`. */
export function ProfilePanel({ openCard }: { openCard?: () => void } = {}) {
  if (!openCard) return null;
  return <div className="profile-panel">
    <button className="link-button" onClick={openCard}>Моя карточка</button>
  </div>;
}

/** Свой логин и смена своего пароля (текущий + новый) на своей карточке. Пароли по-прежнему выдаёт администратор;
 *  «узнать пароль при первом входе» (PIN из SUPPLYMAP) не переносится намеренно. */
export function ChangePassword() {
  const [login, setLogin] = useState("");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let live = true;
    identityApi.profile().then((profile) => { if (live) setLogin(profile.login); }).catch(() => undefined);
    return () => { live = false; };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError("");
    setNotice("");
    if (next !== repeat) {
      setError("Новый пароль и повтор не совпадают.");
      return;
    }
    setBusy(true);
    try {
      await identityApi.changePassword(current, next);
      setCurrent("");
      setNext("");
      setRepeat("");
      setNotice("Пароль изменён.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Пароль не изменён");
    } finally {
      setBusy(false);
    }
  }

  return <div className="change-password">
    {login && <p className="ec-note">Логин: <strong>{login}</strong></p>}
    <form onSubmit={(event) => void submit(event)}>
      <input type="password" aria-label="Текущий пароль" placeholder="Текущий пароль" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} required />
      <input type="password" aria-label="Новый пароль" placeholder="Новый пароль (от 8 символов)" autoComplete="new-password" minLength={8} value={next} onChange={(event) => setNext(event.target.value)} required />
      <input type="password" aria-label="Повтор нового пароля" placeholder="Повтор нового пароля" autoComplete="new-password" value={repeat} onChange={(event) => setRepeat(event.target.value)} required />
      <button className="primary small" type="submit" disabled={busy}>Сменить пароль</button>
    </form>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
  </div>;
}

const APPROVAL_PIN_RE = /^[0-9]{6,8}$/;

/** «Разрешительный ПИН» на своей карточке (владелец 29.10): второй личный ПИН, которым разрешают действия на чужом экране
 *  (сейчас — отгрузку без УПД); не PIN входа. Задаёт и меняет только сам сотрудник, подтверждая текущим паролем или PIN
 *  входа; администратор может только сбросить его в «Сотрудниках». Сам ПИН сервер не возвращает — только «задан с …». */
export function ApprovalPin() {
  const [state, setState] = useState<ApprovalPinState | null>(null);
  const [loadError, setLoadError] = useState("");
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [pin, setPin] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let live = true;
    identityApi.approvalPin()
      .then((loaded) => { if (live) setState(loaded); })
      .catch((reason) => { if (live) setLoadError(failureText(reason, "Не удалось узнать, задан ли разрешительный ПИН")); });
    return () => { live = false; };
  }, []);

  function close() {
    setOpen(false);
    setCurrent("");
    setPin("");
    setRepeat("");
    setError("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError("");
    setNotice("");
    if (!APPROVAL_PIN_RE.test(pin.trim())) {
      setError("Разрешительный ПИН — 6–8 цифр.");
      return;
    }
    if (pin.trim() !== repeat.trim()) {
      setError("Новый разрешительный ПИН и повтор не совпадают.");
      return;
    }
    setBusy(true);
    try {
      const saved = await identityApi.setApprovalPin(current, pin.trim());
      const changed = state?.set ?? false;
      setState(saved);
      close();
      setNotice(changed ? "Разрешительный ПИН изменён." : "Разрешительный ПИН задан.");
    } catch (reason) {
      setCurrent("");
      setError(failureText(reason, "Разрешительный ПИН не сохранён"));
    } finally {
      setBusy(false);
    }
  }

  if (loadError) return <div className="error-banner" role="alert">{loadError}</div>;
  if (!state) return <p className="empty">Загрузка…</p>;
  return <div className="change-password approval-pin">
    <p className="ec-note">Им разрешают действия на чужом экране — например, отгрузку без УПД. Не совпадает с PIN входа.</p>
    <p className="approval-pin-state">
      {state.set
        ? <><span className="tone-ok">Задан</span>{state.set_at && <span className="ec-note"> · с {formatDate(state.set_at)}</span>}</>
        : <span className="tone-warn">Не задан</span>}
      {!open && <button className="secondary small" type="button" onClick={() => { setNotice(""); setOpen(true); }}>{state.set ? "Сменить" : "Задать"}</button>}
    </p>
    {open && <form onSubmit={(event) => void submit(event)} aria-label={state.set ? "Сменить разрешительный ПИН" : "Задать разрешительный ПИН"}>
      <input type="password" aria-label="Текущий пароль или PIN входа" placeholder="Текущий пароль или PIN входа" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} required />
      <input type="password" inputMode="numeric" aria-label="Новый разрешительный ПИН" placeholder="Новый ПИН (6–8 цифр)" autoComplete="new-password" maxLength={8} value={pin} onChange={(event) => setPin(event.target.value)} required />
      <input type="password" inputMode="numeric" aria-label="Повтор разрешительного ПИН" placeholder="Повтор ПИН" autoComplete="new-password" maxLength={8} value={repeat} onChange={(event) => setRepeat(event.target.value)} required />
      <span className="approval-pin-actions">
        <button className="primary small" type="submit" disabled={busy}>{busy ? "Сохраняем…" : "Сохранить ПИН"}</button>
        <button className="secondary small" type="button" disabled={busy} onClick={close}>Отмена</button>
      </span>
    </form>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
  </div>;
}

/** Разрешительный ПИН в ЧУЖОЙ карточке (владелец 29.10): администратор видит, задан ли он и с какого дня, и может сбросить —
 *  сам ПИН не показывается никому. Новый задаёт сам сотрудник в своей карточке. */
export function ApprovalPinAdmin({ userId, name, isSet, setAt, onReset }: {
  userId: string; name: string; isSet: boolean; setAt: string | null; onReset: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function reset() {
    if (busy || !window.confirm(`Сбросить разрешительный ПИН сотрудника ${name}? Новый он задаст сам в своей карточке.`)) return;
    setBusy(true);
    setError("");
    try {
      await staffApi.resetApprovalPin(userId);
      onReset();
    } catch (reason) {
      setError(failureText(reason, "Разрешительный ПИН не сброшен"));
    } finally {
      setBusy(false);
    }
  }
  return <div className="approval-pin">
    <p className="approval-pin-state">
      {isSet
        ? <><span className="tone-ok">Задан</span>{setAt && <span className="ec-note"> · с {formatDate(setAt)}</span>}</>
        : <span className="ec-note">Не задан — задаёт сам сотрудник в своей карточке</span>}
      {isSet && <button className="secondary small" type="button" disabled={busy} onClick={() => void reset()}>Сбросить</button>}
    </p>
    {error && <div className="error-banner" role="alert">{error}</div>}
  </div>;
}
