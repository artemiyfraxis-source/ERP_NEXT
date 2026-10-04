/* Массовая работа со строками таблиц: выбор диапазона по Shift и прогон действия по выбранным строкам
   несколькими параллельными запросами (когда пакетного вызова на сервере нет). */

/** Новый выбор после щелчка по флажку `key`: с Shift — весь диапазон от прошлого щелчка (`anchor`) до этого,
 *  в порядке строк на экране (`order`), получает то же состояние, что и сам флажок. */
export function selectRange(order: string[], current: Set<string>, anchor: string | null, key: string, checked: boolean, shift: boolean): Set<string> {
  const next = new Set(current);
  const from = shift && anchor !== null ? order.indexOf(anchor) : -1;
  const to = order.indexOf(key);
  const keys = from >= 0 && to >= 0 ? order.slice(Math.min(from, to), Math.max(from, to) + 1) : [key];
  for (const item of keys) {
    if (checked) next.add(item); else next.delete(item);
  }
  return next;
}

export type PoolFailure<T> = { item: T; error: unknown };

/** Выполняет `work` для каждой строки, не больше `limit` запросов сразу; `onProgress(сделано)` после каждой.
 *  Ошибка одной строки не останавливает остальные — все отказы возвращаются списком. */
export async function runInPool<T>(
  items: T[],
  work: (item: T) => Promise<unknown>,
  onProgress: (done: number) => void = () => undefined,
  limit = 4,
): Promise<PoolFailure<T>[]> {
  const failures: (PoolFailure<T> & { index: number })[] = [];
  let next = 0;
  let done = 0;
  async function lane() {
    while (next < items.length) {
      const index = next;
      next += 1;
      try {
        await work(items[index]);
      } catch (error) {
        failures.push({ item: items[index], error, index });
      }
      done += 1;
      onProgress(done);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane));
  return failures.sort((a, b) => a.index - b.index).map(({ item, error }) => ({ item, error }));
}
