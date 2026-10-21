import { useState, type FormEvent } from "react";
import { failureText, type KitComponentInput } from "./api";

/** Типы строк состава, как в расчёте; поле свободное — подсказка, а не список. */
const KIT_TYPES = ["Соед", "Терминал", "Провод", "Кабель", "ТУТ", "ТТК", "Маркировка", "Др"];
const EMPTY: KitComponentInput = { type: "", art: "", name: "", per: "" };

/**
 * «+ Комплектующее» в «Данных из листа» (владелец 21.10.2026): технолог дописывает строку в состав КБ, и она сама
 * встаёт в комплектацию проекта. Форма — под таблицей; после добавления очищается для следующей строки.
 */
export function KitComponentAdd({ types, onAdd, onClose }: {
  types: string[];
  onAdd: (row: KitComponentInput) => Promise<void>;
  onClose: () => void;
}) {
  const [row, setRow] = useState<KitComponentInput>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (key: keyof KitComponentInput) => (value: string) => setRow((current) => ({ ...current, [key]: value }));
  const ready = row.art.trim() !== "" && Number(row.per.replace(",", ".")) > 0;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || busy) return;
    setBusy(true); setError("");
    try {
      await onAdd({ ...row, name: row.name.trim() || row.art.trim() });
      setRow(EMPTY);
    } catch (reason) {
      setError(failureText(reason, "Комплектующее не добавлено"));
    } finally {
      setBusy(false);
    }
  }

  const options = [...new Set([...types.filter(Boolean), ...KIT_TYPES])];
  return <form className="kit-add" aria-label="Добавить комплектующее" onSubmit={submit}>
    <input list="kit-add-types" aria-label="Тип" placeholder="Тип" value={row.type} onChange={(event) => set("type")(event.target.value)} />
    <datalist id="kit-add-types">{options.map((type) => <option key={type} value={type} />)}</datalist>
    <input aria-label="Название" placeholder="Название" value={row.name} onChange={(event) => set("name")(event.target.value)} />
    <input aria-label="Артикул" placeholder="Артикул" required value={row.art} onChange={(event) => set("art")(event.target.value)} />
    <input aria-label="Кол-во на изделие" className="num" inputMode="decimal" placeholder="на 1 шт" value={row.per} onChange={(event) => set("per")(event.target.value)} />
    <div className="kit-add-actions">
      <button type="submit" className="primary small" disabled={!ready || busy}>{busy ? "Добавляю…" : "Добавить в комплектацию"}</button>
      <button type="button" className="secondary small" onClick={onClose}>Закрыть</button>
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
  </form>;
}
