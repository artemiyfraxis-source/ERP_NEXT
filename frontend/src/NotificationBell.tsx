import { useEffect, useState } from "react";
import { operationsApi } from "./api";

/** Сигнал ленты «Уведомления» об изменении числа непрочитанных (прочитали на странице). */
export const NOTIFICATIONS_CHANGED = "erp:notifications-changed";
const POLL_MS = 60000;

/** Колокольчик с числом непрочитанных на каждом экране (PortalApp.html paintBell_/notifRefresh). */
export function NotificationBell({ open }: { open: () => void }) {
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    let alive = true;
    const refresh = () => {
      operationsApi.unread()
        .then((result) => { if (alive) setUnread(result.unread); })
        .catch(() => undefined); // молча: сбой опроса не мешает работе
    };
    const changed = (event: Event) => {
      const value = (event as CustomEvent<number>).detail;
      if (typeof value === "number") setUnread(value); else refresh();
    };
    refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    window.addEventListener(NOTIFICATIONS_CHANGED, changed);
    return () => {
      alive = false;
      window.clearInterval(timer);
      window.removeEventListener(NOTIFICATIONS_CHANGED, changed);
    };
  }, []);

  const label = unread > 0 ? `Уведомления: ${unread} непрочитанных` : "Уведомления";
  return <button type="button" className="notif-bell" aria-label={label} title={label} onClick={open}>
    <span aria-hidden="true">🔔</span>
    {unread > 0 && <span className="notif-badge" data-testid="notif-badge">{unread > 99 ? "99+" : unread}</span>}
  </button>;
}
