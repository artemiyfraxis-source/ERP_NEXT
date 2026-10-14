import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { accessLevelLabel, failureText, staffApi, type Employee, type Principal } from "./api";
import { formatMoment } from "./format";

/** «Смотреть как сотрудник» (владелец 12.10.2026): администратор со своей карточки выбирает сотрудника, и весь ERP
 *  показывается как у него — меню, разделы, задания, карточка — только на просмотр (сервер отказывает в любых
 *  изменениях). Плашка сверху на каждой странице держит это на виду и возвращает к себе. */

export function ViewAsBanner({ principal, onExit }: { principal: Principal; onExit: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!principal.viewing) return null;
  const until = formatMoment(principal.viewing.until);

  async function exit() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await onExit();
    } catch (reason) {
      setError(failureText(reason, "Не удалось вернуться к себе"));
      setBusy(false);
    }
  }

  return <div className="view-as-banner" role="status">
    <span>
      Вы смотрите как <strong>{principal.user.name}</strong> (только просмотр)
      {until && <span className="view-as-until"> · до {until}</span>}
    </span>
    {error && <span className="view-as-error" role="alert">{error}</span>}
    <button type="button" className="secondary small" disabled={busy} onClick={() => void exit()}>
      {busy ? "Возвращаем…" : "Вернуться к себе"}
    </button>
  </div>;
}

/** Выбор сотрудника: работающие, кроме себя; поиск по ФИО, отделу, логину, площадке. */
export function ViewAsPicker({ selfId, onPick, onClose }: {
  selfId: string;
  onPick: (employee: Employee) => Promise<void>;
  onClose: () => void;
}) {
  const [people, setPeople] = useState<Employee[] | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    let live = true;
    staffApi.directory()
      .then((directory) => {
        if (!live) return;
        const working = directory.users.filter((user) => user.active && user.id !== selfId);
        setPeople(working.sort((a, b) => a.name.localeCompare(b.name, "ru")));
      })
      .catch((reason) => { if (live) setError(failureText(reason, "Список сотрудников не загружен")); });
    return () => { live = false; };
  }, [selfId]);

  useEffect(() => {
    searchRef.current?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); closeRef.current(); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const shown = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("ru").replace(/ё/g, "е");
    if (!people || !needle) return people ?? [];
    return people.filter((person) => [person.name, person.department, person.login, person.site, levelOf(person)]
      .join(" ").toLocaleLowerCase("ru").replace(/ё/g, "е").includes(needle));
  }, [people, query]);

  async function pick(person: Employee) {
    if (busy) return;
    setBusy(person.id);
    setError("");
    try {
      await onPick(person);
    } catch (reason) {
      setError(failureText(reason, "Не удалось открыть вид сотрудника"));
      setBusy("");
    }
  }

  return createPortal(<div className="dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="move-dialog view-as-dialog panel" role="dialog" aria-modal="true" aria-labelledby="view-as-title">
      <h2 id="view-as-title">Смотреть как сотрудник</h2>
      <p className="page-sub">ERP откроется так, как его видит выбранный сотрудник, — только просмотр, на 2 часа.</p>
      <input ref={searchRef} className="search" type="search" aria-label="Поиск сотрудника" placeholder="ФИО, отдел, логин, площадка…" value={query} onChange={(event) => setQuery(event.target.value)} />
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!people && !error && <div className="empty">Загрузка…</div>}
      {people && <ul className="view-as-list" aria-label="Сотрудники">
        {shown.map((person) => <li key={person.id}>
          <button type="button" className="view-as-person" disabled={Boolean(busy)} onClick={() => void pick(person)}>
            <strong>{person.name}</strong>
            <span>{levelOf(person)}</span>
            <span>{[person.department, person.site].filter(Boolean).join(" · ")}</span>
            {busy === person.id && <span className="view-as-opening">открываем…</span>}
          </button>
        </li>)}
        {!shown.length && <li className="empty">{query ? "Никого не нашли — измените запрос." : "Других работающих сотрудников нет."}</li>}
      </ul>}
      <div className="move-actions">
        <button type="button" className="secondary" onClick={onClose}>Отмена</button>
      </div>
    </div>
  </div>, document.body);
}

function levelOf(person: Employee) {
  return person.access_level_label || accessLevelLabel(person.access_level);
}
