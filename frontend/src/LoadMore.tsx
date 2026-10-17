import { useEffect, useRef } from "react";

/** Подгрузка в конце списка (владелец 17.10.2026: «без нажатия — долистал до конца, подгрузка идёт»). Метка внизу
    списка: попала в окно (с запасом 300 px) — `onMore`. `token` — сколько уже показано: после подгрузки метка
    наблюдается заново и, если всё ещё видна (короткая страница), тянет следующую порцию. Браузер без
    IntersectionObserver — прежняя кнопка «Показать ещё». */
export function LoadMore({ onMore, busy = false, token, className = "load-more" }: {
  onMore: () => void;
  busy?: boolean;
  token: number;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const more = useRef(onMore);
  more.current = onMore;
  const observable = typeof IntersectionObserver !== "undefined";
  useEffect(() => {
    const node = ref.current;
    if (!node || busy || !observable) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) more.current();
    }, { rootMargin: "300px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, [busy, token, observable]);
  if (!observable) {
    return <div className={className}><button type="button" className="secondary small" disabled={busy} onClick={onMore}>{busy ? "Загружаем…" : "Показать ещё"}</button></div>;
  }
  return <div ref={ref} className={className} role="status" aria-live="polite">{busy ? "Загружаем…" : ""}</div>;
}
