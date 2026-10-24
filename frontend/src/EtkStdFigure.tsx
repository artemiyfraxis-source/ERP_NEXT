// Рисунок пункта «Типовые дефекты»: есть рисунки стандарта к строке СТО (код в начале названия) — они в ряд, с меткой
// «Норма» / «Брак» и подписью источника; нет — рисунок «Норма | Брак» общей библиотеки etkDefectArt.ts, как раньше.
import { useEffect, useState } from "react";
import { techcardsApi } from "./api";
import { defectArt } from "./etkDefectArt";
import { ROLE_LABELS, figuresForDefect, loadStdFigures, stdFiguresNow, type StdFigureIndex } from "./stdFigures";

// Те же знаки, что у подписей рисунков библиотеки («✓ Норма» / «✗ Брак»): метка читается и без цвета.
const ROLE_MARKS: Record<string, string> = { norm: "✓", defect: "✗", ref: "" };

/** Рисунки стандартов: один запрос на загрузку страницы, пока он идёт — null. */
export function useStdFigures(): StdFigureIndex | null {
  const [index, setIndex] = useState<StdFigureIndex | null>(stdFiguresNow);
  useEffect(() => {
    if (index) return;
    let live = true;
    void loadStdFigures().then((value) => { if (live) setIndex(value); });
    return () => { live = false; };
  }, [index]);
  return index;
}

export function DefectPicture({ name, note = "", code = "", className }: { name: string; note?: string; code?: string; className: string }) {
  const figures = figuresForDefect(useStdFigures(), name);
  if (!figures.length) return <span className={className} dangerouslySetInnerHTML={{ __html: defectArt(name, note, code) }} />;
  return <div className={`${className} etk-std-figs`}>
    {figures.map((figure) => {
      const url = techcardsApi.stdFigureUrl(figure);
      return <figure className="etk-std-fig" key={figure.id}>
        {ROLE_LABELS[figure.role] && <span className={`etk-std-role ${figure.role}`}>{ROLE_MARKS[figure.role]} {ROLE_LABELS[figure.role]}</span>}
        {/* Мелкую вырезку ГОСТ открыть в полный размер — в новой вкладке. */}
        <a href={url} target="_blank" rel="noreferrer" title="Открыть рисунок в полный размер">
          <img src={url} alt={figure.caption} loading="lazy" width={figure.width} height={figure.height} />
        </a>
        <figcaption>{figure.caption}</figcaption>
      </figure>;
    })}
  </div>;
}
