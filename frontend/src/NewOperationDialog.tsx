import { useEffect, useMemo, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { techcardsApi } from "./api";
import "./NewOperationDialog.css";

/* «Новая операция» в «Базе данных» → Операции: номер, название, пустая или копия, раздел. */

export type NewOperationResult = Awaited<ReturnType<typeof techcardsApi.createOperation>>;

/** Куда показать сообщение 422: под «Номером», под «Названием», у выбора копии или общей строкой. */
export function errorField(message: string): "code" | "name" | "copy" | "other" {
  const text = message.toLowerCase();
  if (text.includes("номер")) return "code";
  if (text.includes("назван")) return "name";
  if (text.includes("копи")) return "copy";
  return "other";
}

export function NewOperationDialog({ operations, onClose, onCreated }: {
  operations: Array<{ code: string; name: string }>;
  onClose: () => void;
  onCreated: (result: NewOperationResult) => void;
}) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [mode, setMode] = useState<"blank" | "copy">("blank");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  const found = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("ru-RU");
    const list = needle ? operations.filter((item) => `${item.code} ${item.name}`.toLocaleLowerCase("ru-RU").includes(needle)) : operations;
    return list.slice(0, 50);
  }, [operations, query]);
  const pickedOp = operations.find((item) => item.code === picked);
  const canSubmit = Boolean(code.trim() && name.trim()) && (mode === "blank" || Boolean(picked));
  const where = error ? errorField(error) : "other";

  function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    techcardsApi.createOperation({
      code: code.trim(), name: name.trim(),
      ...(mode === "copy" ? { copy_of: picked } : {}),
      ...(category.trim() ? { category: category.trim() } : {}),
    }).then(onCreated, (reason) => setError(reason instanceof Error ? reason.message : "Не удалось создать операцию"))
      .finally(() => setBusy(false));
  }

  return createPortal(<div className="dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="panel move-dialog new-op-dialog" role="dialog" aria-label="Новая операция">
      <h2>Новая операция</h2>
      <form className="dialog-form" aria-label="Новая операция" onSubmit={submit}>
        <div className="move-grid two">
          <label>Номер<input aria-label="Номер" autoFocus value={code} aria-invalid={where === "code"} onChange={(event) => setCode(event.target.value)} />
            {where === "code" && <span className="field-error" role="alert">{error}</span>}</label>
          <label>Раздел<input aria-label="Раздел" value={category} placeholder="необязательно" onChange={(event) => setCategory(event.target.value)} /></label>
        </div>
        <label>Название<input aria-label="Название" value={name} aria-invalid={where === "name"} onChange={(event) => setName(event.target.value)} />
          {where === "name" && <span className="field-error" role="alert">{error}</span>}</label>
        <div className="seg-tabs" role="tablist" aria-label="Основа">
          <button type="button" role="tab" aria-selected={mode === "blank"} className={mode === "blank" ? "on" : undefined} onClick={() => setMode("blank")}>Пустая</button>
          <button type="button" role="tab" aria-selected={mode === "copy"} className={mode === "copy" ? "on" : undefined} onClick={() => setMode("copy")}>{pickedOp ? `Копия: ${pickedOp.name || pickedOp.code}` : "Копия: выберите операцию"}</button>
        </div>
        {mode === "copy" && <>
          <label>Поиск операции<input aria-label="Поиск операции" type="search" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
          {found.length === 0 ? <div className="empty">Операций не найдено — измените поиск.</div>
            : <div className="new-op-results" role="group" aria-label="Операции">
              {found.map((item) => <button key={item.code} type="button" aria-pressed={picked === item.code} onClick={() => setPicked(item.code)}>{item.name || item.code}<small>{item.code}</small></button>)}
            </div>}
          {where === "copy" && <span className="field-error" role="alert">{error}</span>}
        </>}
        {error && where === "other" && <div className="error-banner" role="alert">{error}</div>}
        <div className="move-actions">
          <button type="button" className="secondary" onClick={onClose}>Отмена</button>
          <button className="primary" disabled={busy || !canSubmit}>{busy ? "Создаём…" : "Создать операцию"}</button>
        </div>
      </form>
    </div>
  </div>, document.body);
}
