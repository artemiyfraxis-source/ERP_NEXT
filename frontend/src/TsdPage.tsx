import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ApiError,
  failureText,
  identityApi,
  warehouseApi,
  type LoginStaff,
  type Principal,
  type WarehouseDestination,
  type WarehouseLot,
  type WarehouseScan,
  type WarehouseUndoPreview,
} from "./api";
import { devicesApi, TSD_OPTION } from "./devicesApi";
import { LABELS_OPTION, LotLabel } from "./LabelPrint";
import { LoginPage } from "./LoginPage";
import { canAct, canSee } from "./navigation";
import { ScannerCheck } from "./ScannerCheck";
import "./tsd.css";
import { useStageOption, useStageOptionState } from "./StageOptions";

/* Стенд, опция warehouse.tsd — экран ТСД (Roller_Buyer TsdPage): терминал сбора данных склада (AUTOID / SEUIC, Android
   8.1, Chrome 70, сканер-клавиатура) и телефон. Одно большое поле всегда в фокусе: ГРН открывает партию, код места —
   что на нём лежит. Крупные действия, цифры с клавиатуры терминала, после действия поле снова ждёт скан.
   Chrome 70: no flex `gap`, `inset`, `aspect-ratio`, `:is/:where`, `color-mix` on these screens (tsd.css). */

/** A data terminal: its scanner types into the field, the on-screen keyboard only gets in the way. */
const IS_TERMINAL = typeof navigator !== "undefined" && /AutoID|SEUIC|Mertech|Urovo|Zebra|Honeywell|Newland/i.test(navigator.userAgent);
/** A complete ГРН with no Enter after it opens by itself after this pause — some scanner profiles send no suffix. */
export const AUTO_SUBMIT_MS = 400;
/** With the on-screen keyboard: long enough to type a package suffix after the ГРН by hand. */
export const AUTO_SUBMIT_TYPED_MS = 1500;
/** ГРН «FRX.26.0000001», with a package «-П2» or without; typed on the Russian layout too («АКЧю26ю0000001»). */
const GRN_COMPLETE = /^(R&D|[A-Za-zА-Яа-яЁё]{3})[.юЮ]\d{2}[.юЮ]\d{7}([\s\-/_]*[A-Za-zА-Яа-я]{1,2}\d{1,3})?$/;

type Action = "writeoff" | "return" | "move" | "label";

const number = (value: string) => Number(value.replace(",", ".").trim());
const lotTitle = (lot: WarehouseLot) => `${lot.grn}${lot.package ? ` / ${lot.package}` : ""}`;

// ── Signed in: the screen ───────────────────────────────────────────────────────────────────────────────────────────

/** The ТСД screen in its own frame (no sidebar): the bar with who and «Выйти», the scan under it. Without the option or
 *  the warehouse section the shell takes over (`onLeave`), as if the address were unknown. */
export function TsdScreen({ principal, onSignOut, onLeave }: { principal: Principal; onSignOut: () => void; onLeave: () => void }) {
  const on = useStageOptionState(TSD_OPTION);
  const allowed = canSee(principal, "warehouse");
  useEffect(() => {
    if (on === false || !allowed) onLeave();
  }, [on, allowed]);
  if (!on || !allowed) return <div className="screen-center muted">Загружаем…</div>;
  return <div className="tsd-shell">
    <header className="tsd-bar">
      <div className="tsd-bar-who">
        <strong>FRAXIS · склад</strong>
        <span className="tsd-user">{principal.user.name}</span>
      </div>
      <button type="button" className="tsd-bar-btn" onClick={onSignOut}>Выйти</button>
    </header>
    <main className="tsd-main">
      <TsdPage readOnly={!canAct(principal, "warehouse")} />
      <button type="button" className="tsd-help-link tsd-full" onClick={onLeave}>Полная версия ERP</button>
    </main>
  </div>;
}

export function TsdPage({ readOnly }: { readOnly: boolean }) {
  const [code, setCode] = useState("");
  const [keyboard, setKeyboard] = useState(!IS_TERMINAL);
  const [checking, setChecking] = useState(false);
  const [searching, setSearching] = useState(false);
  const [answer, setAnswer] = useState<WarehouseScan | null>(null);
  const [lot, setLot] = useState<WarehouseLot | null>(null);
  const [error, setError] = useState("");
  // «Куда» / «откуда» stays for a run of scans (one project, one person), as in the scan bar of «Склад».
  const [who, setWho] = useState("");
  const codeRef = useRef(code);
  codeRef.current = code;
  const field = useRef<HTMLInputElement>(null);
  const asked = useRef(0);

  function focus() {
    window.setTimeout(() => field.current?.focus(), 0);
  }
  useEffect(focus, []);

  async function open(raw: string) {
    const text = raw.trim();
    if (!text) return;
    setCode("");
    setError("");
    setSearching(true);
    const mine = ++asked.current;
    try {
      const found = await warehouseApi.scan(text);
      if (mine !== asked.current) return;
      if (found.kind === "lot" && found.lots.length === 1) {
        setAnswer(null);
        setLot(found.lots[0]);
      } else {
        setAnswer(found);
        setLot(null);
      }
    } catch (reason) {
      if (mine !== asked.current) return;
      setAnswer(null);
      setLot(null);
      setError(reason instanceof ApiError && reason.status === 404
        ? `Код «${text}» не найден — ни партии, ни места.`
        : failureText(reason, "Скан не выполнен"));
    } finally {
      if (mine === asked.current) setSearching(false);
      focus();
    }
  }

  // A scan that arrives while no field has focus (after a tap on the screen, after sign-in) used to be lost: the keys
  // went to the page. Printable keys outside any field go into the scan field instead.
  useEffect(() => {
    if (checking) return;
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable)) return;
      if (event.ctrlKey || event.altKey || event.metaKey) return;
      if (event.key === "Enter") {
        if (codeRef.current.trim()) void open(codeRef.current);
        return;
      }
      if (event.key && event.key.length === 1) {
        event.preventDefault();
        setCode((current) => current + event.key);
        field.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [checking]);

  // A scanner types the whole code at once — open it right away. Typed by hand (keyboard on), wait longer: a short pause
  // before a package suffix used to open the lot without it. Enter opens at once either way.
  useEffect(() => {
    if (!GRN_COMPLETE.test(code.trim())) return;
    const timer = window.setTimeout(() => void open(code), keyboard ? AUTO_SUBMIT_TYPED_MS : AUTO_SUBMIT_MS);
    return () => window.clearTimeout(timer);
  }, [code, keyboard]);

  function submit(event: FormEvent) {
    event.preventDefault();
    void open(code);
  }

  function changed(updated: WarehouseLot) {
    setLot(updated);
    setAnswer((current) => current && { ...current, lots: current.lots.map((item) => (item.id === updated.id ? updated : item)) });
  }

  if (checking) {
    return <div className="tsd">
      <ScannerCheck onClose={() => { setChecking(false); focus(); }} />
    </div>;
  }

  return <div className="tsd">
    <form className="tsd-scanrow" role="search" onSubmit={submit}>
      <input
        ref={field}
        className="tsd-scan"
        aria-label="ГРН или место"
        placeholder="ГРН или место"
        value={code}
        onChange={(event) => setCode(event.target.value)}
        onKeyDown={(event) => { if (event.key === "Escape") { setCode(""); setAnswer(null); setLot(null); setError(""); } }}
        autoComplete="off"
        autoCapitalize="characters"
        spellCheck={false}
        enterKeyHint="go"
        inputMode={keyboard ? "text" : "none"}
      />
      <button
        type="button"
        className={keyboard ? "tsd-kbd active" : "tsd-kbd"}
        title={keyboard ? "Скрыть клавиатуру" : "Показать клавиатуру — ввести код руками"}
        aria-label={keyboard ? "Скрыть клавиатуру" : "Показать клавиатуру — ввести код руками"}
        aria-pressed={keyboard}
        onClick={() => { setKeyboard(!keyboard); field.current?.blur(); focus(); }}
      >⌨</button>
    </form>

    {searching && <p className="muted">Ищу…</p>}
    {error && <div className="tsd-card">
      <div className="tsd-none" role="alert">{error}</div>
      {/* A code that is «not found» may be a scanner that garbles what it types. */}
      <button type="button" className="tsd-help-link" onClick={() => setChecking(true)}>Сканер вводит не то? Проверить сканер</button>
    </div>}
    {!answer && !lot && !searching && !error && <div className="tsd-card tsd-howto">
      <b>Отсканируйте код</b>
      <ul>
        <li><b>ГРН</b> с этикетки на коробке — откроется партия: остаток и кнопки «Списать», «Вернуть», «Переместить».</li>
        <li><b>Место</b> на стеллаже (например, 12C7) — покажет все партии на этом месте.</li>
      </ul>
      <div className="muted">Нет сканера или этикетка не читается — нажмите ⌨ и введите код руками.</div>
      <button type="button" className="tsd-help-link" onClick={() => setChecking(true)}>Сканер не вводит код? Проверить сканер</button>
    </div>}

    {answer && !lot && <div className="tsd-card">
      <div className="tsd-title">
        {answer.kind === "place" ? <>Место <span className="mono">{answer.place}</span></> : <>ГРН <span className="mono">{answer.code}</span></>}
      </div>
      {answer.lots.length === 0 && <p className="muted">Здесь пусто.</p>}
      {answer.lots.map((item) => <button key={item.id} type="button" className="tsd-lotrow" onClick={() => setLot(item)}>
        <span className="mono">{lotTitle(item)}</span>
        <span>{item.name || item.article}</span>
        <strong>{item.balance} {item.unit || "шт"}</strong>
      </button>)}
    </div>}

    {lot && <TsdLot
      key={lot.id}
      lot={lot}
      readOnly={readOnly}
      who={who}
      setWho={setWho}
      onChanged={changed}
      onBack={answer ? () => { setLot(null); focus(); } : undefined}
      onDone={focus}
    />}
  </div>;
}

type Notice = { text: string; undo: boolean };

function TsdLot({ lot, readOnly, who, setWho, onChanged, onBack, onDone }: {
  lot: WarehouseLot;
  readOnly: boolean;
  who: string;
  setWho: (value: string) => void;
  onChanged: (lot: WarehouseLot) => void;
  onBack?: () => void;
  onDone: () => void;
}) {
  const [action, setAction] = useState<Action | null>(null);
  const [quantity, setQuantity] = useState("");
  const [place, setPlace] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [preview, setPreview] = useState<WarehouseUndoPreview | null>(null);
  const [destinations, setDestinations] = useState<WarehouseDestination[]>([]);
  const undoOn = useStageOption("warehouse.undo");
  const labelsOn = useStageOption(LABELS_OPTION);
  const unit = lot.unit || "шт";

  useEffect(() => {
    if (action !== "return") return;
    let live = true;
    warehouseApi.issuedTo(lot.id).then((answer) => {
      if (live) setDestinations(answer.destinations.filter((item) => Number(item.outstanding) > 0));
    }, () => undefined);
    return () => { live = false; };
  }, [action, lot.id]);

  async function run(operation: () => Promise<{ lot: WarehouseLot }>, message: (updated: WarehouseLot) => string, undoable: boolean) {
    setBusy(true);
    setError("");
    setNotice(null);
    setPreview(null);
    try {
      const updated = (await operation()).lot;
      onChanged(updated);
      setNotice({ text: message(updated), undo: undoable && undoOn });
      setAction(null);
      setQuantity("");
      setPlace("");
      onDone();
    } catch (reason) {
      setError(failureText(reason, "Не выполнено"));
    } finally {
      setBusy(false);
    }
  }

  async function undo() {
    setBusy(true);
    setError("");
    try {
      const answer = await warehouseApi.undo(lot.id, lot.version);
      onChanged(answer.lot);
      setNotice({ text: `Отменено: ${answer.undone}`, undo: false });
      setPreview(null);
      onDone();
    } catch (reason) {
      setError(failureText(reason, "Отмена не выполнена"));
    } finally {
      setBusy(false);
    }
  }

  async function askUndo() {
    setError("");
    setNotice(null);
    try {
      setPreview(await warehouseApi.undoPreview(lot.id));
    } catch (reason) {
      setError(failureText(reason, "Не удалось узнать последнее движение"));
    }
  }

  const amount = quantity.replace(",", ".").trim();
  const ready = action === "move" ? place.trim() !== ""
    : (action === "writeoff" || action === "return") ? number(quantity) > 0 && who.trim() !== "" : false;

  function go(event: FormEvent) {
    event.preventDefault();
    if (!ready || busy) return;
    if (action === "writeoff") {
      void run(() => warehouseApi.writeOff(lot.id, lot.version, amount, who.trim(), ""),
        (updated) => `Списано ${amount} ${unit} → ${who.trim()}. Остаток ${updated.balance} ${unit}`, true);
    } else if (action === "return") {
      void run(() => warehouseApi.returnWriteOff(lot.id, lot.version, amount, who.trim(), ""),
        (updated) => `Возвращено ${amount} ${unit}. Остаток ${updated.balance} ${unit}`, true);
    } else if (action === "move") {
      const from = lot.storage_location || "без места";
      void run(() => warehouseApi.move(lot.id, lot.version, place.trim()),
        (updated) => `Перемещено: ${from} → ${updated.storage_location || "без места"}`, true);
    }
  }

  const actions: Array<{ key: Action; label: string }> = [
    { key: "writeoff", label: "Списать" },
    { key: "return", label: "Вернуть" },
    { key: "move", label: "Переместить" },
    ...(labelsOn ? [{ key: "label" as const, label: "Этикетка" }] : []),
  ];

  return <div className="tsd-card" role="region" aria-label={`Партия ${lotTitle(lot)}`}>
    {onBack && <button type="button" className="tsd-help-link tsd-back" onClick={onBack}>← К списку</button>}
    <div className="tsd-grn mono">{lotTitle(lot)}</div>
    <div className="tsd-title">{lot.name || lot.article}</div>
    <div className="muted">
      {lot.name && lot.article !== lot.name ? `${lot.article} · ` : ""}
      {lot.warehouse} · место <span className="mono">{lot.storage_location || "—"}</span>
      {lot.series ? ` · серия ${lot.series}` : ""}
    </div>
    <div className="tsd-qty">{lot.balance} {unit}</div>
    {lot.available !== lot.balance && <div className="muted">
      доступно {lot.available} · {lot.reserve_note || `в резерве ${lot.reserved}`}
    </div>}

    {notice && <div className="tsd-ok" role="status">
      <span>{notice.text}</span>
      {notice.undo && <button type="button" className="tsd-btn tsd-undo-btn" disabled={busy} onClick={() => void undo()}>Отменить</button>}
    </div>}

    {!readOnly && <div className="tsd-actions">
      {actions.map((item) => <button
        key={item.key}
        type="button"
        className={action === item.key ? "tsd-btn active" : "tsd-btn"}
        aria-pressed={action === item.key}
        onClick={() => { setAction(action === item.key ? null : item.key); setError(""); setNotice(null); setPreview(null); }}
      >{item.label}</button>)}
      {undoOn && <button type="button" className="tsd-btn" onClick={() => void askUndo()}>Отменить последнее</button>}
    </div>}

    {preview && <div className="tsd-undo" role="group" aria-label="Отмена последнего движения">
      {preview.undo ? <>
        <span>Последнее: <b>{preview.undo.label}</b></span>
        <button type="button" className="tsd-btn tsd-go" disabled={busy} onClick={() => void undo()}>Отменить его</button>
      </> : <span>{preview.reason}</span>}
    </div>}

    {action === "label" && <div className="tsd-label"><LotLabel key={lot.version} lotId={lot.id} /></div>}

    {action && action !== "label" && <form className="tsd-form" onSubmit={go}>
      {(action === "writeoff" || action === "return") && <input
        autoFocus
        aria-label="Количество"
        inputMode="decimal"
        autoComplete="off"
        placeholder={action === "return" ? `Сколько вернуть, ${unit} (списано ${lot.written_off})` : `Сколько, ${unit}`}
        value={quantity}
        onChange={(event) => setQuantity(event.target.value)}
      />}
      {(action === "writeoff" || action === "return") && <input
        aria-label={action === "writeoff" ? "Куда" : "Откуда"}
        autoComplete="off"
        placeholder={action === "writeoff" ? "Куда: проект или фамилия" : "Откуда возврат: проект или фамилия"}
        value={who}
        onChange={(event) => setWho(event.target.value)}
      />}
      {action === "return" && destinations.length > 0 && <div className="tsd-picks" aria-label="Куда списывали">
        {destinations.map((item) => <button key={item.name} type="button" className={who === item.name ? "tsd-pick active" : "tsd-pick"}
          onClick={() => setWho(item.name)}>{item.name} · {item.outstanding} {item.unit || unit}</button>)}
      </div>}
      {action === "move" && <input
        autoFocus
        aria-label="Новое место"
        autoComplete="off"
        autoCapitalize="characters"
        placeholder="Новое место — сканируйте или введите"
        value={place}
        onChange={(event) => setPlace(event.target.value)}
      />}
      <button type="submit" className="tsd-btn tsd-go" disabled={!ready || busy}>{busy ? "Сохраняю…" : "Готово"}</button>
    </form>}
    {error && <div className="tsd-error" role="alert">{error}</div>}
  </div>;
}

// ── Not signed in: PIN of an employee ───────────────────────────────────────────────────────────────────────────────

const REMEMBERED = "erp.tsd.who";

function remembered(): LoginStaff | null {
  try {
    const saved = JSON.parse(window.localStorage.getItem(REMEMBERED) || "null");
    return saved && typeof saved.id === "string" && typeof saved.name === "string" ? saved : null;
  } catch {
    return null;
  }
}

function remember(person: LoginStaff | null) {
  try {
    if (person) window.localStorage.setItem(REMEMBERED, JSON.stringify(person));
    else window.localStorage.removeItem(REMEMBERED);
  } catch {
    // a private window or blocked storage: the device simply asks «кто вы» again
  }
}

/** Before sign-in on the ТСД address: the stage with the ТСД screen asks for an employee's PIN; anywhere else (the
 *  option off, production) the ordinary sign-in, exactly as without this screen. */
export function TsdEntry({ onLogin }: { onLogin: (principal: Principal) => void }) {
  const [mode, setMode] = useState<"checking" | "pin" | "password">("checking");
  useEffect(() => {
    let live = true;
    devicesApi.entry().then(() => { if (live) setMode("pin"); }, () => { if (live) setMode("password"); });
    return () => { live = false; };
  }, []);
  if (mode === "checking") return <div className="screen-center muted">Загружаем ERP…</div>;
  if (mode === "password") return <LoginPage onLogin={onLogin} />;
  return <TsdLogin onLogin={onLogin} onPassword={() => setMode("password")} />;
}

export function TsdLogin({ onLogin, onPassword, title = "FRAXIS · склад" }: {
  onLogin: (principal: Principal) => void;
  onPassword?: () => void;
  title?: string;
}) {
  const [staff, setStaff] = useState<LoginStaff[] | null>(null);
  const [query, setQuery] = useState("");
  const [person, setPerson] = useState<LoginStaff | null>(remembered);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revealed, setRevealed] = useState("");
  const pinField = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    identityApi.loginStaff().then((answer) => {
      if (!live) return;
      setStaff(answer.staff);
      // Someone remembered on this device but no longer in the list (dismissed): ask again.
      setPerson((current) => (current && answer.staff.some((item) => item.id === current.id) ? current : null));
    }, (reason) => { if (live) setError(failureText(reason, "Список сотрудников не загрузился")); });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    if (person) window.setTimeout(() => pinField.current?.focus(), 0);
  }, [person]);

  const words = query.toLocaleLowerCase("ru").split(/\s+/).filter(Boolean);
  const matches = (staff ?? []).filter((item) => {
    const text = `${item.name} ${item.department}`.toLocaleLowerCase("ru");
    return words.every((word) => text.indexOf(word) >= 0);
  });
  // A short list is shown whole; a long one after the first letters of the surname.
  const shown = words.length || matches.length <= 8 ? matches.slice(0, 8) : [];

  function choose(next: LoginStaff | null) {
    setPerson(next);
    setPin("");
    setError("");
    setRevealed("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!person || busy) return;
    setBusy(true);
    setError("");
    try {
      const answer = await identityApi.pinLogin(person.id, pin);
      if ("reveal" in answer) {
        // First sign-in with the «узнать PIN» mode on: shown once, put into the field.
        setRevealed(answer.pin);
        setPin(answer.pin);
        return;
      }
      remember(person);
      onLogin(answer);
    } catch (reason) {
      setPin("");
      setError(failureText(reason, "Не удалось войти"));
    } finally {
      setBusy(false);
    }
  }

  return <main className="tsd-gate">
    <form className="tsd-gate-card" onSubmit={(event) => void submit(event)}>
      <h1>{title}</h1>
      <p className="muted">Вход по PIN сотрудника. PIN выдаёт администратор: «Сотрудники» → PIN.</p>
      {!person ? <>
        <input
          className="tsd-field"
          aria-label="Кто вы"
          placeholder="Кто вы: начните фамилию"
          autoComplete="off"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        {staff === null && !error && <p className="muted">Загружаю список…</p>}
        {staff !== null && <div className="tsd-people" role="group" aria-label="Сотрудники">
          {shown.map((item) => <button
            key={item.id} type="button" className="tsd-person" onClick={() => choose(item)}>
            <span>{item.name}</span>{item.department && <small>{item.department}</small>}
          </button>)}
          {words.length > 0 && matches.length === 0 && <p className="muted">Никого не нашлось.</p>}
          {words.length > 0 && matches.length > 8 && <p className="muted">Ещё {matches.length - 8} — уточните фамилию.</p>}
        </div>}
      </> : <>
        <div className="tsd-who">
          <b>{person.name}</b>
          <button type="button" className="tsd-help-link" onClick={() => choose(null)}>Другой сотрудник</button>
        </div>
        <input
          ref={pinField}
          className="tsd-pin"
          type="password"
          aria-label="PIN"
          inputMode="numeric"
          autoComplete="off"
          maxLength={8}
          value={pin}
          onChange={(event) => setPin(event.target.value.replace(/\D/g, ""))}
        />
        {revealed && <div className="tsd-ok" role="status">Ваш PIN: <b className="mono">{revealed}</b> — запишите его, он больше не покажется.</div>}
        <button type="submit" className="tsd-btn tsd-go" disabled={busy || pin.length < 6}>{busy ? "Проверяю…" : "Войти"}</button>
      </>}
      {error && <div className="tsd-error" role="alert">{error}</div>}
      {onPassword && <button type="button" className="tsd-help-link" onClick={onPassword}>Войти по логину и паролю</button>}
    </form>
  </main>;
}
