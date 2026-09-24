import { useEffect, useState } from "react";
import { operationsApi, type NotificationItem } from "./api";

export function NotificationsPage() {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setError("");
    try {
      const result = await operationsApi.notifications();
      setItems(result.notifications);
      setUnread(result.unread);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить уведомления");
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => { void load(); }, []);

  async function markRead(ids: string[] = []) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const result = await operationsApi.markRead(ids);
      setUnread(result.unread);
      setItems((current) => current.map((item) => !ids.length || ids.includes(item.id) ? { ...item, read: true } : item));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось отметить уведомление");
    } finally {
      setBusy(false);
    }
  }

  return <section className="page notifications-page">
    <header className="page-head"><h1>Уведомления</h1><span className="page-count">{unread}</span></header>
    <p className="page-sub">Персональная лента из бизнес-событий ERP. Адресат всегда берётся из вашей сессии.</p>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {unread > 0 && <div className="page-actions"><button className="secondary" disabled={busy} onClick={() => void markRead()}>Прочитать все</button></div>}
    <div className="notification-list">{items.map((item) => <article className={`panel notification-card ${item.read ? "read" : "unread"}`} key={item.id}>
      <div className="procurement-title"><div><strong>{item.title}</strong><small>{new Date(item.created_at).toLocaleString("ru-RU")}{item.reference ? ` · ${item.reference}` : ""}</small></div><span className="status">{item.type}</span></div>
      <p>{item.body}</p>
      {!item.read && <button className="link-button" disabled={busy} onClick={() => void markRead([item.id])}>Отметить прочитанным</button>}
    </article>)}</div>
    {loaded && !items.length && <div className="empty">Новых событий пока нет.</div>}
  </section>;
}
