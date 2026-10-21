import { useEffect, useState, type FormEvent } from "react";
import { identityApi } from "./api";

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
