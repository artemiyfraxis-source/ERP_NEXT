import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { api, type Principal } from "./api";
import { hasNavAccess, NAV, openPlace, type Section } from "./navigation";

/** Глобальный поиск: Ctrl+K или «/» вне поля ввода. Результаты приходят из /api/search уже
 *  отфильтрованными по правам; «Команды» — переходы по разделам, открытым этому человеку. */
export type SearchResult = { group: string; title: string; subtitle: string; section: string; params: Record<string, string> };
type SearchPayload = { query: string; results: SearchResult[]; ms: number };

type Command = { label: string; section: Section; params?: Record<string, string>; words?: string };

// Order = order in the list.
const COMMANDS: Command[] = [
  { label: "Оформить заявку", section: "newrequest" },
  { label: "Сегодня", section: "procurement", params: { tab: "today" }, words: "закупка" },
  { label: "Запросы КП", section: "procurement", params: { tab: "rfq" }, words: "коммерческое предложение" },
  { label: "Счета и поставки", section: "procurement", params: { tab: "orders" }, words: "заказы поставщикам оплата доставка" },
  { label: "Склад", section: "stock", words: "остатки ГРН" },
  { label: "Приёмка", section: "receiving", words: "входной контроль" },
  { label: "Поставщики", section: "procurement", params: { tab: "directory" } },
  { label: "Номенклатура", section: "procurement", params: { tab: "items" }, words: "справочник" },
  { label: "Внутренние проекты", section: "projects", params: { tab: "internal" }, words: "ВП" },
];

// A GRN as the label prints it: «PPM.26.0000001», with a package «FRX.26.0000003-П2».
const GRN_LIKE = /^[a-zа-яё]{2,6}\.\d{2}\.\d{3,}(?:[-\s]\S{1,6})?$/i;
const COMMAND_GROUP = "Команды";
const DEBOUNCE_MS = 120;

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
    for (const command of COMMANDS) {
      if (!sees(command.section)) continue;
      const haystack = fold(`${command.label} ${command.words ?? ""}`);
      if (words.every((word) => haystack.includes(word))) {
        list.push({ key: `c${command.label}`, group: COMMAND_GROUP, title: command.label, subtitle: "Перейти", go: finish(command.section, command.params) });
      }
    }
    return list;
  }, [results, query, principal]);

  useEffect(() => { setActive(0); }, [results, query]);

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
          placeholder="Строка закупки, проект, склад (ГРН), поставщик… или команда" autoComplete="off" spellCheck={false}
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
