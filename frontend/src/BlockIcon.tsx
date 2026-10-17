import type { BlockId } from "./navigation";

/* Значки блоков главного экрана — линейные, цвет берут из текста плитки (вместо эмодзи, аудит дизайна 10.10, М-6). */
const PATHS: Record<BlockId, string[]> = {
  // цех: здание с трубой и зубчатой крышей
  prod: ["M3 20V10l5 3V10l5 3V6h3v14", "M3 20h18", "M17 20v-5h4v5"],
  // корзина
  supply: ["M3 5h2.5l2 10h9.5l2-7H7", "M9.5 19.5h.01", "M16.5 19.5h.01"],
  // коробка
  stock: ["M3.5 7.5 12 3l8.5 4.5v9L12 21l-8.5-4.5z", "M3.5 7.5 12 12l8.5-4.5", "M12 12v9"],
  // столбики отчёта
  mgmt: ["M4 20V11", "M10 20V5", "M16 20v-7", "M3 20h18"],
  // книга
  refs: ["M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z", "M5 17a3 3 0 0 1 3-3h11"],
};

export function BlockIcon({ id }: { id: BlockId }) {
  return <svg className="block-icon" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {PATHS[id].map((d) => <path key={d} d={d} />)}
  </svg>;
}
