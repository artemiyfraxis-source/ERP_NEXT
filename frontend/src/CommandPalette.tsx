import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { api, type Principal } from "./api";
import { hasNavAccess, NAV, openPlace, type Section } from "./navigation";

/** Глобальный поиск: Ctrl+K или «/» вне поля ввода. Результаты приходят из /api/search уже
 *  отфильтрованными по правам; «Команды» — переходы по разделам, открытым этому человеку. */
export type SearchResult = { group: string; title: string; subtitle: string; section: string; params: Record<string, string> };
type SearchPayload = { query: string; results: SearchResult[]; ms: number };

// `search`: offered only once something is typed (the short list on an empty field stays the daily jumps).
type Command = { label: string; section: Section; params?: Record<string, string>; words?: string; search?: boolean };

// Order = order in the list.
const COMMANDS: Command[] = [
  { label: "Оформить заявку", section: "newrequest" },
  { label: "Сегодня", section: "procurement", params: { tab: "today" }, words: "закупка" },
  { label: "Запросы КП", section: "procurement", params: { tab: "rfq" }, words: "коммерческое предложение" },
  { label: "Закупка", section: "procurement", params: { tab: "buy" }, words: "решения кп запросить счёт оплата пп" },
  { label: "Счета и поставки", section: "procurement", params: { tab: "orders" }, words: "заказы поставщикам оплата доставка" },
  { label: "Склад", section: "stock", words: "остатки ГРН" },
  { label: "Склад → Оборудование", section: "stock", params: { view: "equipment" }, words: "тсд терминал сбора данных проверка сканера", search: true },
  { label: "Приёмка", section: "receiving", words: "входной контроль" },
  { label: "Поставщики", section: "purchasingrefs", params: { tab: "directory" }, words: "справочники закупка" },
  { label: "Номенклатура", section: "purchasingrefs", params: { tab: "items" }, words: "справочники закупка" },
  { label: "УПД", section: "primarydocs", params: { tab: "upd" }, words: "первичная документация документы" },
  // No menu items since Витя's edits 2–3: the register and own requests are one search away.
  { label: "Мои заявки", section: "myrequests", words: "личная карточка" },
  { label: "Реестр заявок", section: "requests", words: "заявки передать в закупку" },
  { label: "Внутренние проекты", section: "projects", params: { tab: "internal" }, words: "ВП" },
  { label: "Счета (документация)", section: "primarydocs", params: { tab: "invoices" }, words: "счёт оплата платёжка kaiten", search: true },
  { label: "Цепочка документов", section: "primarydocs", params: { tab: "chain" }, words: "упд счёт заказ", search: true },
  // Экран «Отгрузка» убран (владелец 29.10): отправки и журнал — на «Приёмке с производства».
  { label: "Отправки", section: "intake", params: { tab: "packed" }, words: "отгрузка упаковка места накладная трек перевозчик", search: true },
  { label: "Журнал отгрузки", section: "intake", params: { tab: "journal" }, words: "журнал движения готовой продукции", search: true },
  // «База данных»: каждая вкладка — отдельным переходом (ключи вкладок читает ReferencesPage из `&tab=`).
  { label: "База данных → Операции", section: "references", params: { tab: "templates" }, words: "шаблоны техкарт карточки операций", search: true },
  { label: "База данных → Оборудование", section: "references", params: { tab: "ob" }, words: "станки инструмент аппликаторы", search: true },
  { label: "База данных → Разъёмы", section: "references", params: { tab: "connectors" }, words: "серии каталог разъёмов", search: true },
  { label: "База данных → Коаксиальные разъёмы", section: "references", params: { tab: "coax" }, words: "sma bnc", search: true },
  { label: "База данных → Терминалы", section: "references", params: { tab: "ter" }, words: "контакты обжим", search: true },
  { label: "База данных → Наконечники", section: "references", params: { tab: "lug" }, words: "кольцевые вилочные гильзы", search: true },
  { label: "База данных → Коаксиальные кабели", section: "references", params: { tab: "cab_coax" }, words: "rg провода", search: true },
  { label: "База данных → Монтажные провода", section: "references", params: { tab: "cab_wire" }, words: "мс пв", search: true },
  { label: "База данных → Многожильные кабели", section: "references", params: { tab: "cab_multi" }, search: true },
  { label: "База данных → Кабели Ethernet", section: "references", params: { tab: "cab_lan" }, words: "витая пара lan", search: true },
  { label: "База данных → Оболочки", section: "references", params: { tab: "cov" }, words: "тут ттк оплётка гофра кембрик лента", search: true },
  { label: "База данных → Расходники", section: "references", params: { tab: "mat_use" }, words: "припой флюс материалы", search: true },
  { label: "База данных → Соответствия", section: "references", params: { tab: "suppliers" }, words: "домен поставщик страна изготовитель сечения awg", search: true },
];

// What else a person types to find a menu section (the section's own label and subtitle are searched as well).
const SECTION_WORDS: Partial<Record<Section, string>> = {
  intake: "приёмка с производства готовая продукция упаковка отправка отгрузка",
  stock: "оборудование принтер этикеток тсд сканер",
  operations: "журнал аудит события",
  production: "ресурсное планирование операторы смены",
  prodplan: "план проекты блокеры сроки",
  myops: "мои задания операции цех",
  quality: "отк брак контроль",
  staff: "люди сотрудники отделы",
  access: "права уровни пароли",
  references: "бд справочники",
  handout: "списание выдача в цех",
  issues: "комплектация набрать кб",
};

/** Every menu section plus the explicit jumps above; an explicit jump to the same place wins. */
function allCommands(): Command[] {
  const taken = new Set(COMMANDS.filter((command) => !command.params).map((command) => command.section));
  const fromMenu: Command[] = NAV.filter((item) => item.id !== "card" && !taken.has(item.id)).map((item) => ({
    label: item.label, section: item.id, words: `${item.sub ?? ""} ${SECTION_WORDS[item.id] ?? ""}`, search: true,
  }));
  return [...COMMANDS, ...fromMenu];
}

// A GRN as the label prints it: «PPM.26.0000001», with a package «FRX.26.0000003-П2».
const GRN_LIKE = /^[a-zа-яё]{2,6}\.\d{2}\.\d{3,}(?:[-\s]\S{1,6})?$/i;
const COMMAND_GROUP = "Команды";
const DEBOUNCE_MS = 120;

const OPEN_EVENT = "erp:open-search";

/** Открыть палитру из любого места (строка поиска в меню, кнопка в верхней полосе планшета). */
export function openSearch() {
  window.dispatchEvent(new Event(OPEN_EVENT));
}

/** Видимый вход в общий поиск: над меню, с подсказкой «Ctrl+K». */
export function SearchEntry() {
  return (
    <button type="button" className="search-entry" aria-label="Поиск по ERP" onClick={openSearch}>
      <span className="search-entry-label">Поиск по ERP</span>
      <kbd>Ctrl+K</kbd>
    </button>
  );
}

function fold(text: string): string {
  return text.toLowerCase().replace(/ё/g, "е");
}

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

type Entry = { key: string; group: string; title: string; subtitle: string; go: () => void };

export function CommandPalette({ principal }: { principal: Principal }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [settled, setSettled] = useState("");
  const [error, setError] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener(OPEN_EVENT, show);
    return () => window.removeEventListener(OPEN_EVENT, show);
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const ctrlK = (event.ctrlKey || event.metaKey) && !event.altKey && (event.code === "KeyK" || event.key.toLowerCase() === "k");
      if (ctrlK) {
        event.preventDefault();
        setOpen((current) => !current);
      } else if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !typing(event.target)) {
        event.preventDefault();
        setOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) {
      setQuery("");
      setResults([]);
      setSettled("");
      setError("");
      setActive(0);
      input.current?.focus();
    }
  }, [open]);

  // Debounced, and a stale answer never overwrites a newer one.
  useEffect(() => {
    if (!open) return;
    const text = query.trim();
    if (!text) {
      setResults([]);
      setSettled("");
      setError("");
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api<SearchPayload>(`/api/search?q=${encodeURIComponent(text)}`, { signal: controller.signal })
        .then((payload) => { setResults(payload.results); setSettled(text); setError(""); })
        .catch((reason) => {
          if (controller.signal.aborted) return;
          setResults([]);
          setSettled(text);
          setError(reason instanceof Error ? reason.message : "Поиск не ответил");
        });
    }, DEBOUNCE_MS);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [open, query]);

  const entries = useMemo<Entry[]>(() => {
    const finish = (section: string, params: Record<string, string> = {}) => () => { setOpen(false); openPlace(section, params); };
    const text = query.trim();
    const list: Entry[] = results.map((result, index) => ({
      key: `r${index}`, group: result.group, title: result.title, subtitle: result.subtitle, go: finish(result.section, result.params),
    }));
    // A typed or scanned GRN opens the warehouse on that lot even before (or without) the server's answer.
    const sees = (section: Section) => NAV.some((item) => item.id === section && hasNavAccess(principal, item));
    if (GRN_LIKE.test(text) && sees("stock") && !list.some((entry) => entry.group === "Склад")) {
      list.unshift({ key: "grn", group: "Склад", title: text.toUpperCase(), subtitle: "Открыть склад с этой партией", go: finish("stock", { q: text }) });
    }
    const words = fold(text).split(/\s+/).filter(Boolean);
    for (const command of allCommands()) {
      if (!sees(command.section) || (command.search && !words.length)) continue;
      const haystack = fold(`${command.label} ${command.words ?? ""}`);
      if (words.every((word) => haystack.includes(word))) {
        list.push({ key: `c${command.label}`, group: COMMAND_GROUP, title: command.label, subtitle: "Перейти", go: finish(command.section, command.params) });
      }
    }
    return list;
  }, [results, query, principal]);

  // Выбор встаёт на первую строку при новом ответе или тексте — при рендере, а не эффектом: эффект после показа ответа
  // мог сбросить уже нажатую стрелку.
  const [shownFor, setShownFor] = useState<{ results: typeof results; query: string }>({ results, query });
  if (shownFor.results !== results || shownFor.query !== query) {
    setShownFor({ results, query });
    setActive(0);
  }

  if (!open) return null;

  function onKeyDown(event: ReactKeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!entries.length) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current) => (current + step + entries.length) % entries.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      entries[active]?.go();
    }
  }

  const text = query.trim();
  const groups = [...new Set(entries.map((entry) => entry.group))];
  const searching = Boolean(text) && settled !== text;
  const nothing = Boolean(text) && !searching && !entries.length;
  return (
    <div className="palette-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Поиск" onKeyDown={onKeyDown}>
        <input
          ref={input} className="palette-input" role="combobox" aria-expanded="true" aria-controls="palette-list" aria-label="Поиск по ERP"
          aria-activedescendant={entries[active] ? `palette-${entries[active].key}` : undefined}
          placeholder="Что найти: ГРН, проект, КБ, отгрузка, разъём, поставщик, раздел…" autoComplete="off" spellCheck={false}
          value={query} onChange={(event) => setQuery(event.target.value)}
        />
        <div className="palette-list" id="palette-list" role="listbox" aria-label="Результаты">
          {groups.map((group) => (
            <div key={group} role="group" aria-label={group}>
              <div className="palette-group">{group}</div>
              {entries.map((entry, index) => entry.group === group && (
                <div
                  key={entry.key} id={`palette-${entry.key}`} role="option" aria-selected={index === active}
                  className={index === active ? "palette-item active" : "palette-item"}
                  onMouseMove={() => setActive(index)} onClick={entry.go}
                >
                  <span className="palette-title">{entry.title}</span>
                  {entry.subtitle && <span className="palette-sub">{entry.subtitle}</span>}
                </div>
              ))}
            </div>
          ))}
          {searching && !entries.length && <div className="palette-note">Ищем…</div>}
          {nothing && !error && <div className="palette-note">Ничего не нашлось.</div>}
          {error && <div className="palette-note error" role="alert">{error}</div>}
        </div>
        <div className="palette-foot">↑↓ выбрать · Enter открыть · Esc закрыть</div>
      </div>
    </div>
  );
}
