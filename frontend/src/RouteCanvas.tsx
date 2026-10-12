import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { connectorFace, connectorFaceUnits } from "./connectorArt";
import { renderWireSvg } from "./harness/wire-blank-artwork";
import { ART_ROWS, CARD_KIND_LABEL, contentSize, fullHex, linkGeometry, type CardKind, type FaceThumb, type Rect, type Size, type TubeThumb, type WireThumb } from "./routeCards";
import { formatPerUnit } from "./routeLogic";

/* Холст маршрута в стиле ERP: карточки полуфабрикатов (вид, название, «N комплектующих · шт · мин», рисунок), связи
   ортогональными линиями, на связи — число штук (только когда не 1) и «+» / «×». Один компонент у «Схемы
   полуфабрикатов» генератора и у «Маршрута» позиции с чертежом (харнесс). Перетаскивание, «Расставить», масштаб и
   «По размеру» — когда их передал владелец холста. Клавиатура: Tab по карточкам, Enter — открыть, стрелки — сдвинуть
   (Shift — крупнее), Delete — удалить, Esc — снять выбор. Логика раскладки и связей — routeCards.ts. */

export type CanvasCard = Rect & {
  id: string; kind: CardKind; title: string;
  /** Вторая строка под названием (изделие у готового кабеля). */
  subtitle?: string;
  /** «12 комплектующих · 1 шт · 4,5 мин». */
  meta: string;
  art?: ReactNode;
  body?: ReactNode;
  /** Пометка карточки (например, «части нет в чертеже») — жёлтым под мета-строкой. */
  warning?: string;
};
export type CanvasLink = {
  id: string; from: string; to: string;
  /** Штук входа на одну штуку карточки; 1 и null не показываются. */
  qty?: number | null;
  /** «Нарезанные провода» → «Сторона А собрана» — для подписей кнопок. */
  label: string;
};

const ZOOM_MIN = 0.3, ZOOM_MAX = 1.6, ZOOM_STEP = 0.1;
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
const STEP = 8, BIG_STEP = 40;

function isControl(target: EventTarget | null): boolean {
  return target instanceof Element && !!target.closest("button, input, select, textarea, a, label");
}

export function RouteCanvas({ cards, links, label, selected = null, onSelect, onMove, onInsert, onRemoveLink, onRemoveCard, onAutoLayout,
  tools, fit = "width", controls = true, height, margin = 32 }: {
  cards: CanvasCard[];
  links: CanvasLink[];
  /** Подпись холста для чтения с экрана: «Карточки маршрута». */
  label: string;
  selected?: string | null;
  /** Выбор карточки (щелчок, Enter); null — снять выбор (Esc, щелчок по пустому месту). */
  onSelect?: (id: string | null) => void;
  /** Перетаскивание и стрелки: новые координаты карточки на поле. */
  onMove?: (id: string, x: number, y: number) => void;
  /** «+» на связи: вставить карточку между входом и целью. */
  onInsert?: (linkId: string) => void;
  /** «×» на связи. */
  onRemoveLink?: (linkId: string) => void;
  /** «×» на карточке и Delete. */
  onRemoveCard?: (id: string) => void;
  /** «Расставить» — раскладка рядами заново. */
  onAutoLayout?: () => void;
  /** Свои кнопки слева в полосе холста. */
  tools?: ReactNode;
  /** Начальный масштаб: «width» — по ширине окна (не крупнее 100 %), «all» — весь маршрут в окне. */
  fit?: "all" | "width";
  /** Полоса масштаба; генератор её не показывает — схема всегда по ширине итога. */
  controls?: boolean;
  /** Высота окна холста; без неё окно по высоте содержимого. */
  height?: number | string;
  margin?: number;
}) {
  const viewRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<Size | null>(null);
  const [manualZoom, setManualZoom] = useState<number | null>(null);
  const [preview, setPreview] = useState<{ id: string; x: number; y: number } | null>(null);
  const drag = useRef<{ id: string; x: number; y: number; left: number; top: number; moved: boolean } | null>(null);
  const shown = useMemo(() => cards.map((card) => (preview?.id === card.id ? { ...card, x: preview.x, y: preview.y } : card)), [cards, preview]);
  const content = contentSize(shown, margin);
  // По ширине — сразу (текст карточек остаётся читаемым, длинный маршрут прокручивается вниз); «По размеру» — весь
  // маршрут в окне, даже если мельче.
  const measured = !!view && view.width > 0 && view.height >= 0;
  const byWidth = measured ? clamp(Math.min(1, view.width / content.width), ZOOM_MIN, 1) : 1;
  const whole = measured && view.height > 0 && height !== undefined ? clamp(Math.min(1, view.width / content.width, view.height / content.height), ZOOM_MIN, 1) : byWidth;
  const fitted = fit === "all" ? whole : byWidth;
  const zoom = manualZoom ?? fitted;

  useLayoutEffect(() => {
    const element = viewRef.current;
    if (!element) return undefined;
    const measure = () => setView({ width: element.clientWidth, height: element.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Ctrl + колесо — масштаб (обычное колесо прокручивает холст); слушатель не пассивный, чтобы не масштабировать страницу.
  useEffect(() => {
    const element = viewRef.current;
    if (!element || !controls) return undefined;
    const wheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      setManualZoom((current) => clamp((current ?? fitted) * (event.deltaY < 0 ? 1.1 : 1 / 1.1), ZOOM_MIN, ZOOM_MAX));
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, [controls, fitted]);

  const byId = new Map(shown.map((card) => [card.id, card]));
  const step = (delta: number) => setManualZoom(clamp(Math.round((zoom + delta) * 10) / 10, ZOOM_MIN, ZOOM_MAX));

  function pointerDown(card: CanvasCard, event: ReactPointerEvent<HTMLElement>) {
    if (event.button !== 0 || isControl(event.target) || !(onMove || onSelect)) return;
    drag.current = { id: card.id, x: event.clientX, y: event.clientY, left: card.x, top: card.y, moved: false };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  function pointerMove(event: ReactPointerEvent<HTMLElement>) {
    const current = drag.current;
    if (!current) return;
    const dx = (event.clientX - current.x) / zoom, dy = (event.clientY - current.y) / zoom;
    if (!current.moved && Math.hypot(dx, dy) < 4) return;
    current.moved = true;
    if (onMove) setPreview({ id: current.id, x: Math.max(0, Math.round(current.left + dx)), y: Math.max(0, Math.round(current.top + dy)) });
  }
  function pointerUp() {
    const current = drag.current;
    drag.current = null;
    if (!current) return;
    if (current.moved && onMove && preview) onMove(current.id, preview.x, preview.y);
    else if (!current.moved) onSelect?.(current.id);
    setPreview(null);
  }
  function cardKey(card: CanvasCard, event: ReactKeyboardEvent<HTMLElement>) {
    if (event.target !== event.currentTarget) return;
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect?.(card.id); return; }
    if ((event.key === "Delete" || event.key === "Backspace") && onRemoveCard) { event.preventDefault(); onRemoveCard(card.id); return; }
    const arrows: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const arrow = arrows[event.key];
    if (arrow && onMove) {
      event.preventDefault();
      const size = event.shiftKey ? BIG_STEP : STEP;
      onMove(card.id, Math.max(0, card.x + arrow[0] * size), Math.max(0, card.y + arrow[1] * size));
    }
  }

  const interactive = !!(onSelect || onMove);
  return <div className="rc">
    {controls && <div className="rc-tools" role="toolbar" aria-label="Холст маршрута">
      {tools}
      <span className="rc-tools-right">
        {onAutoLayout && <button type="button" className="secondary small" onClick={onAutoLayout} title="Разложить карточки рядами по порядку сборки">Расставить</button>}
        <span className="rc-zoom" role="group" aria-label="Масштаб">
          <button type="button" className="secondary small" aria-label="Уменьшить" disabled={zoom <= ZOOM_MIN} onClick={() => step(-ZOOM_STEP)}>−</button>
          <output className="rc-zoom-value" aria-live="polite">{Math.round(zoom * 100)} %</output>
          <button type="button" className="secondary small" aria-label="Увеличить" disabled={zoom >= ZOOM_MAX} onClick={() => step(ZOOM_STEP)}>+</button>
          <button type="button" className="secondary small" onClick={() => setManualZoom(whole)} title="Весь маршрут в окне">По размеру</button>
        </span>
      </span>
    </div>}
    <div className={height === undefined ? "rc-view is-auto" : "rc-view"} ref={viewRef} style={height === undefined ? undefined : { height }}
      onKeyDown={(event) => { if (event.key === "Escape" && selected) { event.stopPropagation(); onSelect?.(null); } }}
      onPointerDown={(event) => { if (event.target === event.currentTarget || (event.target as Element).classList?.contains("rc-plane")) onSelect?.(null); }}>
      <div className="rc-stage" style={{ width: content.width * zoom, height: content.height * zoom }}>
        <div className="rc-plane" role="group" aria-label={label} style={{ width: content.width, height: content.height, transform: `scale(${zoom})` }}>
          <svg className="rc-links" width={content.width} height={content.height} aria-hidden="true">
            <defs><marker id="rc-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" orient="auto" markerUnits="userSpaceOnUse">
              <path d="M0,0 L8,4 L0,8 z" /></marker></defs>
            {links.map((link) => {
              const from = byId.get(link.from), to = byId.get(link.to);
              if (!from || !to) return null;
              const active = selected === link.from || selected === link.to;
              return <path key={link.id} className={active ? "rc-link is-active" : "rc-link"} d={linkGeometry(from, to).d} markerEnd="url(#rc-arrow)" />;
            })}
          </svg>
          {links.map((link) => {
            const from = byId.get(link.from), to = byId.get(link.to);
            const qty = link.qty != null && link.qty !== 1 ? link.qty : null;
            if (!from || !to || (qty === null && !onInsert && !onRemoveLink)) return null;
            const { mid } = linkGeometry(from, to);
            return <div key={link.id} className="rc-link-tools" style={{ left: mid.x, top: mid.y }}>
              {qty !== null && <span className="rc-qty" title={`Берёт ${formatPerUnit(qty)} шт на одну штуку`}>{formatPerUnit(qty)} шт</span>}
              {onInsert && <button type="button" className="rc-link-btn" title="Вставить карточку" aria-label={`Вставить карточку: ${link.label}`} onClick={() => onInsert(link.id)}>+</button>}
              {onRemoveLink && <button type="button" className="rc-link-btn is-danger" title="Удалить связь" aria-label={`Удалить связь: ${link.label}`} onClick={() => onRemoveLink(link.id)}>×</button>}
            </div>;
          })}
          {shown.map((card) => <article key={card.id} data-card={card.id}
            className={["rc-card", `is-${card.kind}`, selected === card.id ? "is-selected" : "", interactive ? "is-interactive" : "", preview?.id === card.id ? "is-dragging" : ""].filter(Boolean).join(" ")}
            style={{ left: card.x, top: card.y, width: card.width, height: card.height }}
            tabIndex={interactive ? 0 : undefined} aria-current={selected === card.id ? "true" : undefined}
            aria-label={`${CARD_KIND_LABEL[card.kind]}: ${card.title}. ${card.meta}`}
            onPointerDown={(event) => pointerDown(card, event)} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={() => { drag.current = null; setPreview(null); }}
            onKeyDown={(event) => cardKey(card, event)}>
            <header className="rc-card-head">
              <span className="rc-kind">{CARD_KIND_LABEL[card.kind]}</span>
              {onRemoveCard && <button type="button" className="rc-card-x" title="Удалить карточку" aria-label={`Удалить карточку «${card.title}»`} onClick={() => onRemoveCard(card.id)}>×</button>}
            </header>
            <strong className="rc-title" title={card.title}>{card.title}</strong>
            {card.subtitle && <span className="rc-sub" title={card.subtitle}>{card.subtitle}</span>}
            <span className="rc-meta">{card.meta}</span>
            {card.warning && <span className="rc-warn">{card.warning}</span>}
            {card.art && <div className="rc-art">{card.art}</div>}
            {card.body}
          </article>)}
        </div>
      </div>
    </div>
  </div>;
}

/* ── Рисунки карточек ──────────────────────────────────────────────────────────────────────────────────────────── */

function Overflow({ count, word }: { count: number; word: string }) {
  return count > ART_ROWS ? <span className="rc-more">и ещё {count - ART_ROWS} {word}</span> : null;
}

/** Нарезанные провода: заготовка с концами после резки и «А1:1 → В1:1». */
export function WireThumbs({ wires }: { wires: WireThumb[] }) {
  return <div className="rc-rows">
    {wires.slice(0, ART_ROWS).map((wire) => <div className="rc-row" key={wire.key} title={wire.title}>
      <span className="rc-wire" aria-hidden="true" dangerouslySetInnerHTML={{ __html: wireSvg(wire) }} />
      <span className="rc-row-label">{wire.label}</span>
    </div>)}
    <Overflow count={wires.length} word={wires.length - ART_ROWS === 1 ? "провод" : "проводов"} />
  </div>;
}

function wireSvg(wire: WireThumb): string {
  try {
    return renderWireSvg({ left: wire.left, right: wire.right, color: fullHex(wire.color), title: wire.label })
      .replace(/<title[^>]*>.*?<\/title>/, "").replace(' role="img" aria-labelledby="title"', "");
  } catch { return ""; }
}

/** Нарезанные трубки: отрезок трубки и «Резка ТУТ — 4×20 мм». */
export function TubeThumbs({ tubes }: { tubes: TubeThumb[] }) {
  return <div className="rc-rows">
    {tubes.slice(0, ART_ROWS).map((tube) => <div className="rc-row" key={tube.key} title={tube.label}>
      <svg className="rc-tube" viewBox="0 0 84 16" aria-hidden="true"><rect x="10" y="3" width="64" height="10" rx="3" /><line x1="22" y1="3" x2="22" y2="13" /><line x1="62" y1="3" x2="62" y2="13" /></svg>
      <span className="rc-row-label">{tube.count ? `${tube.count} шт · ` : ""}{tube.label}</span>
    </div>)}
    <Overflow count={tubes.length} word="трубок" />
  </div>;
}

/** Торец разъёма из базы разъёмов (connectorArt) с цветами проводов по контактам; нет в базе — подпись в рамке. */
export function FaceArt({ face, maxW, maxH }: { face: FaceThumb; maxW: number; maxH: number }) {
  if (!face.housing) return <span className="rc-face-none" title={face.label}>{face.key}</span>;
  const units = connectorFaceUnits(face.housing);
  const s = Math.max(6, Math.min(22, maxW / (units + 1), maxH / 2.2));
  let box: { w: number; h: number; body: string };
  try { box = connectorFace(face.housing, 2, 2, s, face.byPin); } catch { return <span className="rc-face-none" title={face.label}>{face.key}</span>; }
  const scale = Math.min(1, maxW / (box.w + 4), maxH / (box.h + 4));
  return <svg className="rc-face" width={Math.ceil((box.w + 4) * scale)} height={Math.ceil((box.h + 4) * scale)} viewBox={`0 0 ${Math.ceil(box.w + 4)} ${Math.ceil(box.h + 4)}`}
    role="img" aria-label={face.label}><title>{face.label}</title><g dangerouslySetInnerHTML={{ __html: box.body }} /></svg>;
}

/** Сторона собрана: торцы разъёмов стороны рядом, под каждым — «А1 · XHP-2». */
export function FacesThumb({ faces }: { faces: FaceThumb[] }) {
  const shown = faces.slice(0, 3);
  return <div className="rc-faces">
    {shown.map((face) => <figure key={face.key} className="rc-face-cell" title={face.label}>
      <FaceArt face={face} maxW={Math.floor(200 / shown.length)} maxH={44} />
      <figcaption>{face.label}</figcaption>
    </figure>)}
    {faces.length > shown.length && <span className="rc-more">+{faces.length - shown.length}</span>}
  </div>;
}

/** Готовый кабель: торец А — кабель — торец В. */
export function FinalThumb({ a, b, color = "#6b7c86" }: { a: FaceThumb[]; b: FaceThumb[]; color?: string }) {
  return <div className="rc-final">
    {a[0] ? <figure className="rc-face-cell" title={a[0].label}><FaceArt face={a[0]} maxW={72} maxH={44} /><figcaption>{a[0].key}</figcaption></figure> : <span className="rc-end" />}
    <span className="rc-cable" style={{ background: color }} aria-hidden="true" />
    {b[0] ? <figure className="rc-face-cell" title={b[0].label}><FaceArt face={b[0]} maxW={72} maxH={44} /><figcaption>{b[0].key}</figcaption></figure> : <span className="rc-end" title="Свободные концы" />}
  </div>;
}
