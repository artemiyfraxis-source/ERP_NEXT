import { useEffect, useState } from "react";
import { warehouseApi, type WarehouseShelfPlaces } from "./api";

// SK-15 (SUPPLYMAP placeNextNum_ / wh05PlaceTaken_): «rack + shelf + №», cell optional. Advice only, never a block.
const CODE = /^0*(\d{1,2})([A-Z])0*(\d{1,4})([A-Z])?$/;
const SHELF_PREFIX = /^\s*0*(\d{1,2})\s*([A-Za-zА-Яа-я])/;

function normalized(value: string) {
  return value.replace(/\s+/g, "").toUpperCase();
}

function parse(value: string) {
  const match = CODE.exec(normalized(value));
  if (!match) return null;
  const shelf = `${Number(match[1])}${match[2]}`;
  return { shelf, number: Number(match[3]), code: `${shelf}${Number(match[3])}${match[4] ?? ""}` };
}

export interface PlaceHintState {
  hint: { shelf: string; next: number | null } | null;
  busy: boolean;
}

/** What the shelf named in `place` already holds (server) plus `pending` places not yet entered (same warehouse). */
export function usePlaceHint(warehouse: string, place: string, pending: string[] = []): PlaceHintState {
  const [shelfPlaces, setShelfPlaces] = useState<WarehouseShelfPlaces | null>(null);
  const prefix = SHELF_PREFIX.exec(place);
  const shelfQuery = prefix ? `${Number(prefix[1])}${prefix[2].toUpperCase()}` : "";

  useEffect(() => {
    setShelfPlaces(null);
    if (!shelfQuery) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void warehouseApi.shelfPlaces(warehouse, shelfQuery).then(
        (result) => { if (!cancelled) setShelfPlaces(result); },
        () => undefined, // an unknown shelf or a failed hint is simply no hint
      );
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [warehouse, shelfQuery]);

  if (!shelfPlaces) return { hint: null, busy: false };
  const taken = new Set(shelfPlaces.occupied);
  const numbers = new Set(shelfPlaces.occupied.map((code) => parse(code)?.number));
  for (const other of pending) {
    const parsed = parse(other);
    if (parsed?.shelf === shelfPlaces.shelf) { taken.add(parsed.code); numbers.add(parsed.number); }
  }
  let next = 1;
  while (numbers.has(next) && next <= 999) next += 1;
  const typed = parse(place);
  return {
    hint: { shelf: shelfPlaces.shelf, next: next <= 999 ? next : null },
    busy: Boolean(typed && typed.shelf === shelfPlaces.shelf && taken.has(typed.code)),
  };
}

export const BUSY_TITLE = "Здесь уже лежит другая позиция. Так можно — просто проверь, что это не ошибка.";

export function PlaceHint({ hint, busy, place, onPick }: PlaceHintState & { place: string; onPick: (code: string) => void }) {
  if (!hint) return null;
  const suggestion = hint.next === null ? "" : `${hint.shelf}${hint.next}`;
  return <p className={busy ? "warning-inline" : "page-sub"} role="status">
    {busy && <>{BUSY_TITLE} </>}
    {suggestion
      ? <>На полке {hint.shelf} первый свободный № — {hint.next}. {normalized(place) !== suggestion && <button type="button" className="link-button" onClick={() => onPick(suggestion)}>Подставить {suggestion}</button>}</>
      : <>На полке {hint.shelf} свободных номеров нет.</>}
  </p>;
}
