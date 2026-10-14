import { useEffect, useState } from "react";
import { techcardsApi } from "./api";
import { latinArt, normalizeTerminalArt, pairsFromSeries, type TerminalPairs } from "./terminalPick";

/* Пары «корпус → терминалы» базы разъёмов (connector-series по артикулу корпуса) — для выбора терминала в «Сложной
   сборке» (terminalPick.ts). Нет базы или серии — пар нет, терминал ищется по составу и БД.ТЕР. */

const cache = new Map<string, Promise<string[]>>();
/** Тесты: пары читаются заново. */
export function resetTerminalPairs() { cache.clear(); }

function pairsOf(art: string): Promise<string[]> {
  let found = cache.get(art);
  if (!found) {
    found = Promise.resolve().then(() => techcardsApi.connectorSeries(latinArt(art))).then((answer) => pairsFromSeries(art, answer)).catch(() => []);
    cache.set(art, found);
  }
  return found;
}

export function useTerminalPairs(arts: string[]): TerminalPairs {
  const [pairs, setPairs] = useState<TerminalPairs>({});
  const key = [...new Set(arts.filter(Boolean))].sort().join("\n");
  useEffect(() => {
    let alive = true;
    const list = key ? key.split("\n") : [];
    void Promise.all(list.map(async (art) => [normalizeTerminalArt(art), await pairsOf(art)] as const))
      .then((items) => { if (alive) setPairs(Object.fromEntries(items.filter(([, terms]) => terms.length))); });
    return () => { alive = false; };
  }, [key]);
  return pairs;
}
