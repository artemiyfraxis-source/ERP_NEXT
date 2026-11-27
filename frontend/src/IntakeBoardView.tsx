import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { COLUMNS, GROUPS, matchesFilter, NO_FILTER, PAGE_SIZE, type BoardCard, type BoardFilter, type CardAction, type ColumnId, type ProductionGroup } from "./intakeBoard";
import { plural } from "./format";

/* Доска «Приёмка с производства»: пять колонок по стадиям, поиск и чипы над ними. Карточка — проект или отправка;
   клик открывает окно, главная кнопка двигает дальше, «⋯» — остальное. Первые 20 карточек колонки, дальше «ещё N». */

export interface MenuItem { label: string; run: () => void; disabled?: boolean }
const FOLD_KEY = "production-intake:groups:v1";
const loadFolded = (): Record<ProductionGroup, boolean> => {
  try { return { production: false, waiting: true, ...JSON.parse(window.localStorage.getItem(FOLD_KEY) ?? "{}") }; }
  catch { return { production: false, waiting: true }; }
};

function CardMenu({ items, label }: { items: MenuItem[]; label: string }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => { if (!box.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);
  if (!items.length) return null;
  return <span className="board-menu" ref={box} onClick={(event) => event.stopPropagation()}>
    <button type="button" className="secondary small board-menu-btn" aria-haspopup="menu" aria-expanded={open} aria-label={`Ещё действия: ${label}`}
      onClick={() => setOpen(!open)} onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }}>⋯</button>
    {open && <span className="board-menu-list" role="menu">{items.map((item) => <button key={item.label} type="button" role="menuitem" disabled={item.disabled}
      onClick={() => { setOpen(false); item.run(); }}>{item.label}</button>)}</span>}
  </span>;
}

function Card({ card, busy, onOpen, onAction, menu }: {
  card: BoardCard; busy: boolean; onOpen: (card: BoardCard) => void; onAction: (card: BoardCard, action: CardAction) => void; menu: MenuItem[];
}) {
  const title = `${card.project}${card.customer ? ` · ${card.customer}` : ""}`;
  return <article className="board-card" data-column={card.column} data-card={card.id} onClick={() => onOpen(card)}>
    <div className="board-card-head">
      <button type="button" className="board-card-title" aria-label={`Открыть ${title}`} onClick={(event) => { event.stopPropagation(); onOpen(card); }}>
        <strong>{card.project}</strong>{card.customer && <span className="board-card-customer"> · {card.customer}</span>}</button>
    </div>
    <div className="board-card-kb" title={card.kbLine}>{card.kbLine}</div>
    <div className="board-card-delivery muted">{card.deliveryLine}</div>
    {(card.code || card.sequence) && <div className="board-card-code">{card.sequence && <span>{card.sequence}</span>}{card.code && <span className="board-card-pl">{card.code}</span>}</div>}
    {card.note && <div className="board-card-note muted">{card.note}</div>}
    {card.blocker
      ? <div className="board-card-blocker" role="note"><span className="tone-warn">⚠ {card.blocker.text}</span><small className="muted"> — {card.blocker.who}</small></div>
      : card.okText ? <div className="board-card-ok tone-ok">✓ {card.okText}</div> : null}
    {(card.action || menu.length > 0) && <div className="board-card-actions" onClick={(event) => event.stopPropagation()}>
      {card.action && <button type="button" className={card.action.disabled ? "secondary small" : "primary small"} disabled={card.action.disabled || busy}
        title={card.action.note} onClick={() => onAction(card, card.action!)}>{card.action.label}</button>}
      {card.action?.note && <small className="muted board-card-actionnote">{card.action.note}</small>}
      <CardMenu items={menu} label={title} />
    </div>}
  </article>;
}

function Cards({ cards, busy, onOpen, onAction, menuOf, empty }: {
  cards: BoardCard[]; busy: boolean; onOpen: (card: BoardCard) => void; onAction: (card: BoardCard, action: CardAction) => void;
  menuOf: (card: BoardCard) => MenuItem[]; empty: string;
}) {
  const [shown, setShown] = useState(PAGE_SIZE);
  const rest = cards.length - shown;
  if (!cards.length) return <div className="board-empty muted">{empty}</div>;
  return <>
    {cards.slice(0, shown).map((card) => <Card key={card.id} card={card} busy={busy} onOpen={onOpen} onAction={onAction} menu={menuOf(card)} />)}
    {rest > 0 && <button type="button" className="board-more" onClick={() => setShown(shown + PAGE_SIZE)}>ещё {rest} · показать</button>}
  </>;
}

const EMPTY: Record<ColumnId, string> = {
  production: "В производстве и ожидании ничего нет.", accepted: "Принятого и не упакованного нет.", packed: "Упакованного, которому не хватает данных, нет.",
  to_ship: "Готового к отгрузке нет — нужны УПД, ТК и трек.", shipped: "Отгруженного за 30 дней нет.",
};

export function IntakeBoard({ cards, busy, initial, picked, onOpen, onAction, menuOf, columnTools, onJournal }: {
  cards: BoardCard[]; busy: boolean; initial: Partial<BoardFilter>; picked: ColumnId | null;
  onOpen: (card: BoardCard) => void; onAction: (card: BoardCard, action: CardAction) => void; menuOf: (card: BoardCard) => MenuItem[];
  /** Кнопки в шапке колонки («Отгрузить все готовые»). */
  columnTools: (column: ColumnId, shown: BoardCard[]) => ReactNode;
  onJournal: () => void;
}) {
  const [filter, setFilter] = useState<BoardFilter>({ ...NO_FILTER, ...initial });
  const [folded, setFolded] = useState(loadFolded);
  const set = (patch: Partial<BoardFilter>) => setFilter((current) => ({ ...current, ...patch }));
  const toggleGroup = (group: ProductionGroup) => setFolded((current) => {
    const next = { ...current, [group]: !current[group] };
    try { window.localStorage.setItem(FOLD_KEY, JSON.stringify(next)); } catch { /* память браузера недоступна */ }
    return next;
  });
  const customers = useMemo(() => [...new Set(cards.map((card) => card.customer).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru")), [cards]);
  const visible = useMemo(() => cards.filter((card) => matchesFilter(card, filter)), [cards, filter]);
  const filtered = Boolean(filter.query.trim() || filter.mine || filter.gaps || filter.hot || filter.customer);
  const total = (column: ColumnId, list: BoardCard[]) => list.filter((card) => card.column === column);
  const kbCount = (list: BoardCard[]) => list.reduce((sum, card) => sum + (card.shipment ? new Set(card.shipment.lines.filter((line) => Number(line.picked) > 0).map((line) => `${line.project}${line.item_index}`)).size : card.kbs.length), 0);

  return <div className="board">
    <div className="board-tools">
      <input type="search" className="board-search" aria-label="Поиск по доске" placeholder="Проект, КБ, ГРН, PL, DN, № УПД, трек, получатель"
        value={filter.query} onChange={(event) => set({ query: event.target.value })} />
      <button type="button" className={`board-chip${filter.mine ? " on" : ""}`} aria-pressed={filter.mine} onClick={() => set({ mine: !filter.mine })}>Мои действия</button>
      <button type="button" className={`board-chip${filter.gaps ? " on" : ""}`} aria-pressed={filter.gaps} onClick={() => set({ gaps: !filter.gaps })}>Только с пробелами</button>
      <button type="button" className={`board-chip${filter.hot ? " on" : ""}`} aria-pressed={filter.hot} onClick={() => set({ hot: !filter.hot })}>Срок горит</button>
      <select className="board-customer" aria-label="Заказчик" value={filter.customer} onChange={(event) => set({ customer: event.target.value })}>
        <option value="">Заказчик: все</option>{customers.map((name) => <option key={name} value={name}>{name}</option>)}
      </select>
      {filtered && <button type="button" className="link-button" onClick={() => setFilter(NO_FILTER)}>Сбросить</button>}
    </div>
    <div className="board-columns">
      {COLUMNS.map(([column, title]) => {
        const all = total(column, cards); const list = total(column, visible);
        return <section key={column} className={`board-column${picked === column ? " is-picked" : ""}`} data-column={column} aria-label={title}>
          <header className="board-column-head">
            <h2>{title}</h2>
            <span className="board-count" title="Карточек; серым — КБ">{filtered ? `${list.length} из ${all.length}` : list.length}
              {column !== "production" && <span className="muted"> · {kbCount(list)} КБ</span>}</span>
          </header>
          {columnTools(column, list)}
          <div className="board-column-body">
            {column === "production" ? GROUPS.map(([group, groupTitle]) => {
              const own = list.filter((card) => card.group === group);
              const open = !folded[group];
              return <div key={group} className="board-group">
                <button type="button" className="board-group-head" aria-expanded={open} onClick={() => toggleGroup(group)}>
                  <span aria-hidden="true">{open ? "▾" : "▸"}</span> {groupTitle} · {own.length} {plural(own.length, ["проект", "проекта", "проектов"])} · {kbCount(own)} КБ</button>
                {open && <Cards cards={own} busy={busy} onOpen={onOpen} onAction={onAction} menuOf={menuOf} empty={group === "production" ? "В производстве ничего нет." : "В ожидании ничего нет."} />}
              </div>;
            }) : <>
              <Cards cards={list} busy={busy} onOpen={onOpen} onAction={onAction} menuOf={menuOf} empty={filtered ? "Ничего не найдено." : EMPTY[column]} />
              {column === "shipped" && <button type="button" className="board-journal link-button" onClick={onJournal}>Старше 30 дней — в «Журнале»</button>}
            </>}
          </div>
        </section>;
      })}
    </div>
  </div>;
}
