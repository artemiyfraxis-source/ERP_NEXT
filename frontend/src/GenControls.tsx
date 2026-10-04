import type { InputHTMLAttributes, ReactNode } from "react";

/* Элементы формы генератора техкарт (владелец 04.10: «дизайн генератора топорный»): сегменты вместо радиокнопок
   браузера, переключатели и чипы вместо флажков, единица измерения внутри поля, выбор карточки операции — по-русски. */

/** Один из вариантов — сегментами; повторный клик по выбранному ничего не меняет (форма не перерисовывается зря). */
export function Seg<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: ReadonlyArray<readonly [T, ReactNode, ReactNode?]>; onChange: (value: T) => void;
}) {
  return <div className="gseg" role="radiogroup" aria-label={label}>
    {options.map(([key, text, hint]) => <button key={key} type="button" role="radio" aria-checked={value === key} className={value === key ? "on" : ""}
      onClick={() => { if (value !== key) onChange(key); }}>{text}{hint ? <small>{hint}</small> : null}</button>)}
  </div>;
}

/** Обработка «вкл/выкл»: переключатель с подписью и серой подсказкой; включённая раскрывает свои поля под собой. */
export function Toggle({ checked, onChange, title, hint, children }: {
  checked: boolean; onChange: (on: boolean) => void; title: ReactNode; hint?: ReactNode; children?: ReactNode;
}) {
  return <div className={checked ? "gopt on" : "gopt"}>
    <label className="gsw">
      <input type="checkbox" role="switch" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span className="gsw-text"><span>{title}</span>{hint ? <small>{hint}</small> : null}</span>
    </label>
    {checked && children ? <div className="gopt-body">{children}</div> : null}
  </div>;
}

/** Флажок-чип: провод, конец, параметр разъёма. */
export function Chip({ checked, onChange, children, title }: { checked: boolean; onChange: (on: boolean) => void; children: ReactNode; title?: string }) {
  return <label className={checked ? "gchip on" : "gchip"} title={title}>
    <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />{children}
  </label>;
}

/** Поле с единицей внутри («мм», «сек»). */
export function UnitInput({ unit, ...props }: InputHTMLAttributes<HTMLInputElement> & { unit: string }) {
  return <span className="gunit" data-unit={unit}><input {...props} /></span>;
}

/** Подпись над полем. */
export function Field({ label, hint, className, children }: { label: ReactNode; hint?: ReactNode; className?: string; children: ReactNode }) {
  return <label className={className ? `gfield ${className}` : "gfield"}><span className="gfield-label">{label}{hint ? <small>{hint}</small> : null}</span>{children}</label>;
}

export type CardOption = { id: string; label: string; code: string };

/** Карточка операции: в поле — русское название, в списке — «название · код» по категориям (одинаковых названий
    много — «Пайка» у двух десятков карточек, код их различает). Под полем остаётся обычный select. */
export function CardSelect({ label, value, groups, byId, warn, onChange }: {
  label: string; value: string; groups: Array<[string, CardOption[]]>; byId: Map<string, CardOption>; warn?: boolean; onChange: (id: string) => void;
}) {
  const current = value ? byId.get(value) : undefined;
  return <span className={warn ? "gpick warn" : "gpick"}>
    <span className="gpick-face" aria-hidden="true">{current ? current.label : "— не выбрана —"}</span>
    <select aria-label={label} title={current ? [current.label, current.code].filter(Boolean).join(" · ") : undefined} value={value}
      onChange={(event) => onChange(event.target.value)}>
      <option value="">— не выбрана —</option>
      {groups.map(([group, items]) => <optgroup key={group} label={group}>
        {items.map((item) => <option key={item.id} value={item.id}>{item.code ? `${item.label} · ${item.code}` : item.label}</option>)}
      </optgroup>)}
    </select>
  </span>;
}

/** Галочка «нормы из БД.ОП» — значок с подсказкой вместо служебной строки. */
export function CheckMark({ title }: { title: string }) {
  return <span className="gmark ok" role="img" aria-label={title} title={title}>
    <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7" /></svg>
  </span>;
}
