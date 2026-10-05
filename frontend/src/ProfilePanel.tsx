import { useState, type FormEvent } from "react";
import { accessLevelLabel, identityApi, type OwnProfile } from "./api";

/** The employee's own profile in the sidebar footer (SUPPLYMAP portalState_: role, department, site) and the change of
 *  their own password (current + new). Passwords are still issued by the administrator; there is no «узнать пароль
 *  при первом входе» (SUPPLYMAP PIN onboarding is not ported on purpose). */
export function ProfilePanel({ openCard }: { openCard?: () => void } = {}) {
  const [open, setOpen] = useState(false);
  const [profile, setProfile] = useState<OwnProfile | null>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function toggle() {
    const opening = !open;
    setOpen(opening);
    setError("");
    setNotice("");
    if (opening && !profile) {
      try {
        setProfile(await identityApi.profile());
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Профиль не загружен");
      }
    }
  }

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

  return <div className="profile-panel">
    {openCard && <button className="link-button" onClick={openCard}>Моя карточка</button>}
    <button className="link-button" aria-expanded={open} onClick={() => void toggle()}>Профиль</button>
    {open && <div className="profile-card">
      {profile && <dl>
        <dt>Уровень доступа</dt><dd>{profile.access_level_label || (profile.access_level ? accessLevelLabel(profile.access_level) : profile.role_label)}</dd>
        {profile.job_title && <><dt>Должность</dt><dd>{profile.job_title}</dd></>}
        <dt>Отдел</dt><dd>{profile.department || "—"}</dd>
        <dt>Площадка</dt><dd>{profile.site ? `${profile.site} · ${profile.site_label}` : profile.site_label}</dd>
        <dt>Логин</dt><dd>{profile.login}</dd>
      </dl>}
      <form onSubmit={(event) => void submit(event)}>
        <strong>Сменить пароль</strong>
        <input type="password" aria-label="Текущий пароль" placeholder="Текущий пароль" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} required />
        <input type="password" aria-label="Новый пароль" placeholder="Новый пароль (от 8 символов)" autoComplete="new-password" minLength={8} value={next} onChange={(event) => setNext(event.target.value)} required />
        <input type="password" aria-label="Повтор нового пароля" placeholder="Повтор нового пароля" autoComplete="new-password" value={repeat} onChange={(event) => setRepeat(event.target.value)} required />
        <button className="primary small" type="submit" disabled={busy}>Сменить пароль</button>
      </form>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <div className="success-banner">{notice}</div>}
    </div>}
  </div>;
}
