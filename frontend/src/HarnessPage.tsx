import { Suspense, useEffect, useRef, useState } from "react";
import { failureText, kitsetsApi } from "./api";
import { HarnessEditorLazy, loadHarnessAdapters, type ComponentPlacementApi, type HarnessEditorCatalogApi } from "./harness";
import type { ServerHarnessDesignApi, ServerSaveState } from "./harness/serverApi";
import { HarnessHistory } from "./HarnessHistory";
import { closeHarness } from "./harnessNav";
import { readFileBase64 } from "./ProjectsPage";

const STATE_TEXT: Record<ServerSaveState, string> = {
  saved: "Сохранено",
  saving: "Сохраняю…",
  conflict: "Конфликт: чертёж изменили — обновить",
  error: "Не сохранено",
};

/** Чертёж жгута на весь экран: редактор TECHMAP-GRAPHER над чертежом из КД позиции. `catalog` — справочники ERP
 *  (по умолчанию редактор берёт свой пустой каталог в памяти). Без права «Проекты: правка» — тот же чертёж в режиме
 *  просмотра: двигать поле, масштабировать, выбирать и смотреть свойства; ничего не меняется и не сохраняется. */
export function HarnessPage({ project, index, canEdit, catalog, onBack = () => closeHarness(project) }: {
  project: string; index: string; canEdit: boolean; catalog?: HarnessEditorCatalogApi; onBack?: () => void;
}) {
  const [title, setTitle] = useState("");
  const [api, setApi] = useState<ServerHarnessDesignApi | null>(null);
  const [placements, setPlacements] = useState<ComponentPlacementApi | undefined>(undefined);
  const [erpCatalog, setErpCatalog] = useState<HarnessEditorCatalogApi | undefined>(undefined);
  const [state, setState] = useState<ServerSaveState>("saved");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [generation, setGeneration] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    kitsetsApi.get(project)
      .then((card) => { if (live) setTitle(card.assemblies.find((item) => item.index === index)?.name ?? ""); })
      .catch((reason) => { if (live) setError(failureText(reason, "Позиция не прочитана")); });
    return () => { live = false; };
  }, [project, index]);

  // Редактор и его модель грузятся отдельным куском вместе с адаптером; при «обновить» адаптер создаётся заново.
  // Библиотечные компоненты пишутся в тот же документ КД через тот же адаптер (одна версия на двоих).
  useEffect(() => {
    let live = true;
    setApi(null); setState("saved");
    void Promise.all([import("./harness/serverApi"), loadHarnessAdapters()]).then(([{ createServerHarnessDesignApi }, adapters]) => {
      if (!live) return;
      // Справочники ERP (провода, терминалы, разъёмы…) — по умолчанию; `catalog` подменяет их.
      const references = catalog ?? adapters.createErpHarnessEditorCatalog();
      const designApi = createServerHarnessDesignApi({ readOnly: !canEdit, onState: (next) => { if (live) setState(next); } });
      setErpCatalog(references);
      setPlacements(references.templates ? designApi.componentPlacements(references.templates) : undefined);
      setApi(designApi);
    }).catch((reason) => { if (live) setError(failureText(reason, "Редактор не загрузился")); });
    return () => { live = false; };
  }, [canEdit, generation]);

  async function importZip(file: File | undefined) {
    if (!file) return;
    if (!window.confirm(`Загрузить «${file.name}»? Чертёж ${index} в ERP будет заменён содержимым файла.`)) return;
    setError(""); setNote(`Загружаю ${file.name}…`);
    try {
      const current = await kitsetsApi.harness(project, index);
      await kitsetsApi.importHarness(project, index, { file_name: file.name, content_b64: await readFileBase64(file), revision: current.revision });
      setNote("Чертёж загружен.");
      setGeneration((value) => value + 1);
    } catch (reason) { setNote(""); setError(failureText(reason, "Чертёж не загружен")); }
  }

  // «Сохранить версию»: автосохранения одного сотрудника за 10 минут — одна строка истории; кнопка закрепляет текущую.
  async function checkpoint() {
    if (!api) return;
    setError("");
    try {
      const fixed = await kitsetsApi.checkpointHarness(project, index, api.serverRevision);
      setNote(fixed.revision ? `Версия ${fixed.revision} сохранена в истории.` : "Чертёж ещё не сохранён — сохранять в историю нечего.");
    } catch (reason) { setNote(""); setError(failureText(reason, "Версия не сохранена")); }
  }

  const heading =`${index}${title ? ` ${title}` : ""}`;
  return <section className="harness-page" aria-label={`Чертёж жгута ${heading}`}>
    <header className="harness-page-head">
      <button className="secondary small" onClick={onBack}>← Назад</button>
      <h1>{heading}</h1>
      <span className="muted">Чертёж жгута</span>
      {canEdit
        ? <span className={`harness-save tone-${state === "conflict" || state === "error" ? "danger" : state === "saving" ? "warn" : "ok"}`} role="status">{STATE_TEXT[state]}</span>
        : <span className="harness-save tone-neutral" role="status" title="Правят сотрудники с правом «Проекты: правка»">Только просмотр</span>}
      {state === "conflict" && <button className="primary small" onClick={() => setGeneration((value) => value + 1)}>Обновить чертёж</button>}
      <span className="harness-page-actions">
        {note && <span className="muted">{note}</span>}
        {canEdit && <button className="secondary small" disabled={!api || state !== "saved"} onClick={() => void checkpoint()}
          title="Текущая сохранённая версия останется в истории отдельной строкой">Сохранить версию</button>}
        <button className="secondary small" onClick={() => setHistoryOpen(true)}>История версий</button>
        <a className="secondary small button-link" href={kitsetsApi.harnessExportUrl(project, index)} download>Скачать для TECHMAP-GRAPHER</a>
        {canEdit && <button className="secondary small" onClick={() => fileRef.current?.click()}>Загрузить .techmap-project.zip</button>}
      </span>
      <input ref={fileRef} type="file" accept=".zip" hidden data-testid="harness-zip" onChange={(event) => { void importZip(event.target.files?.[0]); event.target.value = ""; }} />
    </header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="harness-page-body">
      {!api ? <div className="empty">Загрузка…</div>
        : <Suspense fallback={<div className="empty">Загрузка редактора…</div>}>
          <HarnessEditorLazy key={generation} projectId={project} harnessId={index} harnessDesignation={heading} initialView="e4"
            apiOverride={api} componentPlacementApiOverride={placements} catalogOverride={erpCatalog} readOnly={!canEdit} />
        </Suspense>}
    </div>
    {historyOpen && <HarnessHistory project={project} index={index} canEdit={canEdit} onClose={() => setHistoryOpen(false)}
      onRestored={(from, revision) => { setError(""); setNote(`Восстановлена версия ${from} — теперь это версия ${revision}.`); setGeneration((value) => value + 1); }} />}
  </section>;
}
