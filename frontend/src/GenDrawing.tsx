/*
 * «Построить чертёж» на вкладках «Коакс» и «Ручная сборка» генератора ТК: кнопка с причиной, почему строить не из чего,
 * и итог построения со ссылкой «Открыть чертёж». Сам чертёж строит configToHarness.ts (лениво) — тем же путём, что и
 * типовые вкладки.
 */
export type DrawNote = { text: string; error: boolean; warnings: string[] };

const obj = (value: unknown): Record<string, unknown> => (value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {});

/** Почему по конфигурации сборки чертёж не построить ("" — можно): нужны соединения по распиновке или коакс с двумя
 *  разъёмами. */
export function configDrawingProblem(config: Record<string, unknown>): string {
  if (Array.isArray(config.connections) && config.connections.length) return "";
  if (config.coax && typeof config.coax === "object") {
    const coax = obj(config.coax);
    if (!obj(coax.sideA).article) return "Не выбран разъём стороны А";
    if (!obj(coax.sideB).article) return "Сторона В без разъёма — чертёж строится от разъёма до разъёма";
    return "";
  }
  return "В сборке нет соединений по распиновке";
}

/** Кнопка в шапке блока; не из чего строить — причина серым рядом, кнопка недоступна. */
export function DrawingButton({ busy, exists, problem, onBuild }: { busy: boolean; exists: boolean; problem: string; onBuild: () => void }) {
  return <>
    {problem && <span className="gsec-meta">{problem}</span>}
    <button type="button" className="secondary small" disabled={busy || !!problem}
      title={problem || "Чертёж жгута позиции по разъёмам и проводам сборки"} onClick={onBuild}>
      {busy ? "Строю чертёж…" : exists ? "Обновить чертёж" : "Построить чертёж"}</button>
  </>;
}

/** Итог построения: что построено, «Открыть чертёж КБn», замечания свёрнуто; ошибка — красной плашкой. */
export function DrawingNoteView({ note, index, onOpen }: { note: DrawNote | null; index: string; onOpen?: () => void }) {
  if (!note) return null;
  return <div className={note.error ? "error-banner gen-drawing-note" : "gen-drawing-note"} role={note.error ? "alert" : "status"}>
    <span>{note.text}</span>
    {!note.error && onOpen && <button type="button" className="link-button" onClick={onOpen}>Открыть чертёж {index}</button>}
    {note.warnings.length > 0 && <details><summary>Замечания: {note.warnings.length}</summary>
      <ul>{note.warnings.map((item) => <li key={item}>{item}</li>)}</ul></details>}
  </div>;
}
