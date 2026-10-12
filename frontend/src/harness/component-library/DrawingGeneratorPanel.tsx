import { DraftNumberInput } from "./DraftNumberInput";
import { InfoHint } from "../InfoHint";
import { generatorRoles, generatorLayout, type DrawingGenerator, type GeneratorRole } from "./drawing-generator";

export function DrawingGeneratorPanel({ generator: g, mode, articleId, articles, change, assign, apply, choose, setMode, create, previewPeriods, setPreviewPeriods }: {
  generator?: DrawingGenerator; mode: "source" | "article"; articleId: string | null;
  articles: { id: string; articleKey: string; count: number }[];
  change: (g: DrawingGenerator) => void; assign: (role: GeneratorRole) => void;
  apply: (ids: string[]) => void; choose: (id: string) => void;
  setMode: (mode: "source" | "article") => void; create: () => void;
  previewPeriods: number | undefined; setPreviewPeriods: (value: number | undefined) => void;
}) {
  if (!g) return <div className="array-layout-toolbar"><button onClick={create}>Создать генератор</button><InfoHint>Исходник хранится один раз. Назначьте выделению роли: начало, период, конец, статичные фигуры. Период включает фигуры и контактные точки. Прежние массивы продолжают работать в своих видах.</InfoHint></div>;
  const index = Math.max(0, articles.findIndex(a => a.id === articleId));
  const article = articles[index];
  let summary = "Исходник в работе · можно сохранить без генерации";
  try { const layout = article && g.roles.period.length && g.periodPointIds.length && generatorLayout(g, article.count); if (layout) summary = `${article!.articleKey} · ${article!.count} конт. · ${layout.repeats} периодов`; } catch (e) { summary = (e as Error).message; }
  const disabled = mode === "article";
  return <div className="array-layout-toolbar" aria-label="Генератор рисунков">
    <label>Режим<select aria-label="Режим генератора" value={mode} onChange={e=>setMode(e.target.value as "source"|"article")}><option value="source">Исходник</option><option value="article" disabled={!g.articles.some(a=>a.articleId===articleId)}>Артикул</option></select></label>
    {disabled && <button onClick={()=>setMode("source")}>Редактировать исходник</button>}
    <span className="generator-roles">{generatorRoles.map(role=><button disabled={disabled} key={role} onClick={()=>assign(role)}>{({start:"Начало",period:"Период",end:"Конец",static:"Статичные"})[role]} · {g.roles[role].length}</button>)}</span>
    <label>Рост<select aria-label="Ось генератора" disabled={disabled} value={g.axis} onChange={e=>change({...g,axis:e.target.value as DrawingGenerator["axis"]})}><option value="horizontal">Горизонтально</option><option value="vertical">Вертикально</option></select></label>
    {([['pitch','Шаг'],['rowPitch','Между рядами'],['baseColumns','Базовых колонок']] as const).map(([key,label])=><label key={key}>{label}<DraftNumberInput aria-label={label} min={key==='baseColumns'?1:0.1} max={key==='baseColumns'?1000:10000} step={key==='baseColumns'?1:0.1} disabled={disabled} value={g[key]} onValueChange={value=>change({...g,[key]:value})}/></label>)}
    <label>Ряды<select aria-label="Ряды генератора" disabled={disabled} value={g.rows} onChange={e=>change({...g,rows:Number(e.target.value)})}>{[1,2,3,4].map(n=><option key={n}>{n}</option>)}</select></label>
    <label>Нумерация<select aria-label="Обход контактов" disabled={disabled} value={g.traversal} onChange={e=>change({...g,traversal:e.target.value as DrawingGenerator['traversal']})}><option value="along">Вдоль</option><option value="across">Поперёк</option></select></label>
    <label>Переход<select aria-label="Переход нумерации" disabled={disabled} value={g.numbering} onChange={e=>change({...g,numbering:e.target.value as DrawingGenerator['numbering']})}><option value="new-row">Новый ряд</option><option value="snake">Змейка</option></select></label>
    <label className="generator-reverse"><input type="checkbox" disabled={disabled} checked={g.reverse} onChange={e=>change({...g,reverse:e.target.checked})}/>Обратно</label>
    <label>Первый угол<select aria-label="Первый угол нумерации" value={g.corner} disabled={disabled} onChange={e=>change({...g,corner:e.target.value as DrawingGenerator['corner']})}>{Object.entries({"top-left":"Слева сверху","top-right":"Справа сверху","bottom-left":"Слева снизу","bottom-right":"Справа снизу"}).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
    <button disabled={disabled||!article} onClick={()=>article&&apply([article.id])}>Для артикула</button><button disabled={disabled||!articles.length} onClick={()=>apply(articles.map(a=>a.id))}>Применить к серии</button>
    <div className="generator-variant-controls"><label className="generator-variants">Вариант<input aria-label="Вариант генератора" type="range" min={0} max={Math.max(0,articles.length-1)} value={index} disabled={!articles.length} onChange={e=>choose(articles[Number(e.target.value)]!.id)}/></label>
    <button aria-label="Предыдущий вариант" disabled={index===0} onClick={()=>choose(articles[index-1]!.id)}>‹</button><button aria-label="Следующий вариант" disabled={index>=articles.length-1} onClick={()=>choose(articles[index+1]!.id)}>›</button></div>
    <output>{summary}</output>
    <label>Проверить N<DraftNumberInput aria-label="Проверочное число периодов" min={1} max={1000} placeholder="по артикулу" value={previewPeriods??""} disabled={disabled} onEmpty={()=>setPreviewPeriods(undefined)} onValueChange={setPreviewPeriods}/></label>
    <InfoHint>Рисуйте торцы для базового числа колонок. Конец смещается на разницу колонок × шаг; начало и статичные фигуры остаются на месте. В «Исходнике» редактируйте прототип на основном поле, результат выбранного варианта показан рядом. В режиме «Артикул» основа защищена, новые фигуры принадлежат только ему. Ползунок не изменяет документ. Контакты нумеруются по колонке № таблицы; {'{{n}}'} в тексте периода показывает номер первого контакта ячейки.</InfoHint>
  </div>;
}
