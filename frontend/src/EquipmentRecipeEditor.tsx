import { useEffect, useRef, useState } from "react";
import { failureText } from "./api";
import { PageWindow } from "./PageWindow";
import { equipmentSetupApi, type RecipeField, type SetupMachine, type SetupRecipe } from "./equipmentSetupApi";

export function EquipmentRecipeEditor({ machine, initial, saved, close }: {
  machine: SetupMachine; initial?: SetupRecipe; saved: (recipe: SetupRecipe) => void; close: () => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [source, setSource] = useState(initial?.source ?? "");
  const [geometry, setGeometry] = useState<RecipeField[]>(initial?.geometry ?? []);
  const [panel, setPanel] = useState<RecipeField[]>(initial?.panel_settings ?? [{ label: "", value: "", unit: "" }]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const recipeId = useRef(initial?.recipe_id ?? crypto.randomUUID());
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const dismiss = () => { if (!busy && (!dirty || window.confirm("Закрыть без сохранения рецепта?"))) close(); };
  function fields(label: string, values: RecipeField[], update: (values: RecipeField[]) => void) {
    return <section className="setup-fields"><h3>{label === "Панель" ? "Ручные настройки панели" : "Геометрия кабеля"}</h3>
      {values.map((field, index) => <div className="setup-field-row" key={index}>
        <label>Параметр<input name={`${label}-${index}-label`} autoComplete="off" aria-label={`${label}: параметр ${index + 1}`} required maxLength={160} value={field.label} onChange={event => update(values.map((item, i) => i === index ? { ...item, label: event.target.value } : item))} /></label>
        <label>Значение<input name={`${label}-${index}-value`} autoComplete="off" aria-label={`${label}: значение ${index + 1}`} required maxLength={500} value={field.value} onChange={event => update(values.map((item, i) => i === index ? { ...item, value: event.target.value } : item))} /></label>
        <label>Ед.<input name={`${label}-${index}-unit`} autoComplete="off" aria-label={`${label}: единица ${index + 1}`} maxLength={40} value={field.unit} onChange={event => update(values.map((item, i) => i === index ? { ...item, unit: event.target.value } : item))} /></label>
        <button type="button" className="secondary small" aria-label={`Удалить ${label.toLowerCase()} ${index + 1}`} disabled={busy || (label === "Панель" && values.length === 1)} onClick={() => update(values.filter((_, i) => i !== index))}>Удалить</button>
      </div>)}
      <button type="button" className="secondary small" disabled={busy || values.length >= 100} onClick={() => { setDirty(true); update([...values, { label: "", value: "", unit: "" }]); }}>Добавить параметр</button>
    </section>;
  }
  return <PageWindow label="Ручной рецепт наладки" close={dismiss}><form className="panel equipment-setup" onChange={() => setDirty(true)} onSubmit={async event => {
    event.preventDefault(); event.stopPropagation(); if (busy) return; setBusy(true); setError("");
    try {
      const recipe = await equipmentSetupApi.publish(recipeId.current, { machine_id: machine.id, title: title.trim(), source: source.trim(), geometry, panel_settings: panel, expected_version: initial?.version ?? 0 });
      setDirty(false); saved(recipe);
    } catch (reason) { setError(failureText(reason, "Не удалось сохранить рецепт")); } finally { setBusy(false); }
  }}>
    <div className="page-head"><h2>{initial ? `Новая версия рецепта · ${initial.version + 1}` : "Новый ручной рецепт"}</h2><button type="button" className="secondary" disabled={busy} onClick={dismiss}>Закрыть</button></div>
    <p><strong>{machine.id}</strong> · {machine.name} · {machine.site}</p>
    {error && <p className="error-banner" role="alert">{error}</p>}
    <div className="setup-grid"><label>Название рецепта<input autoComplete="off" name="title" required maxLength={200} value={title} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
      <label>Источник настроек<input autoComplete="off" name="source" required maxLength={2000} value={source} disabled={busy} onChange={event => setSource(event.target.value)} /></label></div>
    {fields("Геометрия", geometry, setGeometry)}{fields("Панель", panel, setPanel)}
    <div className="setup-actions"><button className="primary" disabled={busy}>{busy ? "Сохраняю…" : "Сохранить версию"}</button></div>
  </form></PageWindow>;
}
