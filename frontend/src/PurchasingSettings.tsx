import { useEffect, useState } from "react";
import { failureText, identityApi } from "./api";
import { hasFullAccess } from "./navigation";
import { purchasingDirectoryApi, type PurchasingSetting } from "./purchasingDirectoryApi";

function shown(value: PurchasingSetting["value"]) {
  return typeof value === "boolean" ? (value ? "да" : "нет") : String(value);
}

/** «Настройки закупки» — гейт выбора, НДС, сроки. Читают все с доступом к закупкам, меняет администратор. */
export function PurchasingSettings({ readOnly = false }: { readOnly?: boolean } = {}) {
  const [rows, setRows] = useState<PurchasingSetting[] | null>(null);
  const [draft, setDraft] = useState<Record<string, string | boolean>>({});
  const [admin, setAdmin] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    purchasingDirectoryApi.settings()
      .then((next) => { if (live) { setRows(next); setDraft({}); } })
      .catch((reason) => { if (live) setError(failureText(reason, "Не удалось загрузить настройки")); });
    identityApi.me().then((me) => { if (live) setAdmin(hasFullAccess(me)); }).catch(() => undefined);
    return () => { live = false; };
  }, []);

  const canEdit = admin && !readOnly;
  const current = (row: PurchasingSetting) => draft[row.key] ?? (typeof row.value === "boolean" ? row.value : String(row.value));
  const dirty = Object.keys(draft).length > 0;

  async function save() {
    if (!rows) return;
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const changes: Record<string, number | string | boolean> = {};
      for (const row of rows) {
        const value = draft[row.key];
        if (value === undefined) continue;
        changes[row.key] = typeof value === "boolean" ? value : value.trim().replace(",", ".");
      }
      setRows(await purchasingDirectoryApi.saveSettings(changes));
      setDraft({});
      setSaved(true);
    } catch (reason) {
      setError(failureText(reason, "Не удалось сохранить настройки"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page">
      <div className="page-head"><h1>Настройки закупки</h1></div>
      {!canEdit && <p className="page-sub">Меняет администратор.</p>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {saved && <div className="success-banner" role="status">Настройки сохранены</div>}
      {!rows ? (!error && <p className="muted">Загрузка…</p>) : (
        <div className="panel purchasing-settings">
          {rows.map((row) => (
            <div className="purchasing-setting" key={row.key}>
              <label>
                <span>{row.label}</span>
                {row.kind === "bool" ? (
                  <input
                    type="checkbox"
                    aria-label={row.label}
                    checked={Boolean(current(row))}
                    disabled={!canEdit}
                    onChange={(event) => setDraft({ ...draft, [row.key]: event.target.checked })}
                  />
                ) : (
                  <input
                    type="number"
                    aria-label={row.label}
                    inputMode="decimal"
                    step={row.kind === "int" ? 1 : "any"}
                    min={row.minimum ?? undefined}
                    max={row.maximum ?? undefined}
                    value={String(current(row))}
                    disabled={!canEdit}
                    onChange={(event) => setDraft({ ...draft, [row.key]: event.target.value })}
                  />
                )}
              </label>
              <p className="page-sub">{row.hint}</p>
              <span className="muted">по умолчанию: {shown(row.default)}</span>
            </div>
          ))}
          {canEdit && (
            <div className="form-actions">
              <button className="primary compact" disabled={busy || !dirty} onClick={save}>Сохранить</button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
