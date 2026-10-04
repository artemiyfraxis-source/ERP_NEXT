import { useEffect, useMemo, useState } from "react";
import { operationsApi, type NotificationItem } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDate } from "./format";
import { NOTIFICATIONS_CHANGED } from "./NotificationBell";
import { notificationKind } from "./notificationLabels";

function deletedText(count: number) {
  return count > 0 ? `Удалено уведомлений: ${count}` : "Удалять нечего";
}

export function NotificationsPage() {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

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

  /** Новое число непрочитанных — на странице и в колокольчике на каждом экране. */
  function publishUnread(count: number) {
    setUnread(count);
    window.dispatchEvent(new CustomEvent(NOTIFICATIONS_CHANGED, { detail: count }));
  }

  /** Одно действие за раз: ошибка показывается баннером, строки остаются как были. */
  async function run(failure: string, action: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await action();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : failure);
      throw reason;
    } finally {
      setBusy(false);
    }
  }

  /** Кнопки вне таблицы: ошибка уже в баннере, ждать результат им нечего. */
  const quietly = (action: Promise<void>) => { void action.catch(() => undefined); };

  function markRead(ids: string[] = []) {
    return run("Не удалось отметить уведомление", async () => {
      const result = await operationsApi.markRead(ids);
      publishUnread(result.unread);
      setItems((current) => current.map((item) => !ids.length || ids.includes(item.id) ? { ...item, read: true } : item));
    });
  }

  function deleteOne(id: string) {
    return run("Не удалось удалить уведомление", async () => {
      const result = await operationsApi.deleteNotification(id);
      publishUnread(result.unread);
      setItems((current) => current.filter((item) => item.id !== id));
    });
  }

  /** Выбранные строки (ids) или «Удалить все» / «Удалить прочитанные» (scope). */
  function deleteMany(selector: { scope: "all" | "read" } | { ids: string[] }) {
    return run("Не удалось удалить уведомления", async () => {
      const result = await operationsApi.deleteNotifications(selector);
      publishUnread(result.unread);
      setItems((current) => current.filter((item) =>
        "ids" in selector ? !selector.ids.includes(item.id) : selector.scope === "read" && !item.read));
      setNotice(deletedText(result.deleted));
    });
  }

  const columns = useMemo<DataColumn<NotificationItem>[]>(() => [
    { key: "at", title: "Получено", width: 140, value: (item) => item.created_at, render: (item) => formatDate(item.created_at) },
    {
      key: "kind", title: "Тип", width: 150, value: (item) => notificationKind(item.type).label, filter: true,
      render: (item) => {
        const kind = notificationKind(item.type);
        return <span className={`kit-status tone-${kind.tone}`}>{kind.label}</span>;
      },
    },
    {
      key: "title", title: "Событие", wrap: true, value: (item) => `${item.title} ${item.body}`,
      render: (item) => <><strong className="notification-title">{item.title}</strong>{item.body && <span className="notification-body">{item.body.split(/\n+/).join(" · ")}</span>}</>,
    },
    { key: "reference", title: "Номер", value: (item) => item.reference },
    {
      key: "actions", title: "Действия", width: 150, value: () => "",
      render: (item) => <span className="row-actions" onClick={(event) => event.stopPropagation()}>
        {!item.read && <button type="button" className="link-button" disabled={busy} onClick={() => quietly(markRead([item.id]))}>Прочитано</button>}
        <button type="button" className="link-button danger-link" disabled={busy} onClick={() => quietly(deleteOne(item.id))}>Удалить</button>
      </span>,
    },
  ], [busy]); // eslint-disable-line react-hooks/exhaustive-deps

  const hasRead = items.some((item) => item.read);

  return <section className="page notifications-page">
    <header className="page-head"><h1>Уведомления</h1><span className="page-count">{unread > 0 ? `${unread} непрочитанных` : "всё прочитано"}</span>
      {items.length > 0 && <div className="page-actions">
        {unread > 0 && <button type="button" className="secondary" disabled={busy} onClick={() => quietly(markRead())}>Прочитать все</button>}
        {hasRead && <button type="button" className="secondary" disabled={busy} onClick={() => quietly(deleteMany({ scope: "read" }))}>Удалить прочитанные</button>}
        <button type="button" className="secondary" disabled={busy} onClick={() => quietly(deleteMany({ scope: "all" }))}>Удалить все</button>
      </div>}
    </header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner" role="status">{notice}</div>}
    {loaded && <DataTable
      label="Уведомления" rows={items} columns={columns} rowKey={(item) => item.id} storageKey="notifications"
      emptyText="Новых событий пока нет."
      rowClassName={(item) => item.read ? "notif-read" : "notif-unread"}
      presets={[{ label: "Непрочитанные", test: (item) => !item.read }]}
      bulkActions={[
        { label: "Прочитано", disabled: (rows) => rows.every((item) => item.read), run: (rows) => markRead(rows.map((item) => item.id)) },
        { label: "Удалить выбранные", run: (rows) => deleteMany({ ids: rows.map((item) => item.id) }) },
      ]}
      renderDetail={(item) => <p className="notification-detail">{item.body}</p>}
    />}
    {!loaded && !error && <div className="empty">Загрузка…</div>}
  </section>;
}
