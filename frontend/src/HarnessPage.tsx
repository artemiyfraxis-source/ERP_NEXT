import { Suspense, useEffect, useRef, useState } from "react";
import { failureText, kitsetsApi } from "./api";
import { HarnessEditorLazy, type HarnessEditorCatalogApi } from "./harness";
import type { ServerHarnessDesignApi, ServerSaveState } from "./harness/serverApi";
import { readFileBase64 } from "./ProjectsPage";

/** Адрес полноэкранного чертежа жгута позиции проекта. */
export function harnessHref(project: string, index: string) {
  const url = new URL(window.location.href);
  for (const key of ["section", "block", "project", "user", "harness"]) url.searchParams.delete(key);
  url.searchParams.set("section", "projects");
  url.searchParams.set("project", project);
  url.searchParams.set("harness", index);
  return `${url.pathname}${url.search}`;
}

function go(href: string) {
  window.history.pushState({}, "", href);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export const openHarness = (project: string, index: string) => go(harnessHref(project, index));
export const closeHarness = (project: string) => {
  const url = new URL(window.location.href);
  url.searchParams.delete("harness");
  url.searchParams.set("section", "projects");
  url.searchParams.set("project", project);
  go(`${url.pathname}${url.search}`);
};

const STATE_TEXT: Record<ServerSaveState, string> = {
  saved: "Сохранено",
  saving: "Сохраняю…",
  conflict: "Конфликт: чертёж изменили — обновить",
  error: "Не сохранено",
};

/** Чертёж жгута на весь экран: редактор TECHMAP-GRAPHER над чертежом из КД позиции. `catalog` — справочники ERP
 *  (по умолчанию редактор берёт свой пустой каталог в памяти). */
export function HarnessPage({ project, index, canEdit, catalog, onBack = () => closeHarness(project) }: {
  project: string; index: string; canEdit: boolean; catalog?: HarnessEditorCatalogApi; onBack?: () => void;
}) {
  const [title, setTitle] = useState("");
  const [api, setApi] = useState<ServerHarnessDesignApi | null>(null);
  const [state, setState] = useState<ServerSaveState>("saved");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [generation, setGeneration] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    kitsetsApi.get(project)
      .then((card) => { if (live) setTitle(card.assemblies.find((item) => item.index === index)?.name ?? ""); })
      .catch((reason) => { if (live) setError(failureText(reason, "Позиция не прочитана")); });
    return () => { live = false; };
  }, [project, index]);

  // Редактор и его модель грузятся отдельным куском вместе с адаптером; при «обновить» адаптер создаётся заново.
  useEffect(() => {
    if (!canEdit) return;
    let live = true;
    setApi(null); setState("saved");
    void import("./harness/serverApi").then(({ createServerHarnessDesignApi }) => {
      if (live) setApi(createServerHarnessDesignApi({ onState: (next) => { if (live) setState(next); } }));
    }).catch((reason) => { if (live) setError(failureText(reason, "Редактор не загрузился")); });
    return () => { live = false; };
  }, [canEdit, generation]);

  async function importZip(file: File | undefined) {
    if (!file) return;
    if (!window.confirm(`Загрузить «${file.name}»? Чертёж ${index} в ERP будет заменён содержимым файла.`)) return;
    setError(""); setNote(`Загружаю ${file.name}…`);
    try {
      const current = await kitsetsApi.harness(project, index);
      await kitsetsApi.importHarness(project, index, { file_name: file.name, content_b64: await readFileBase64(file), version: current.version });
      setNote("Чертёж загружен.");
      setGeneration((value) => value + 1);
    } catch (reason) { setNote(""); setError(failureText(reason, "Чертёж не загружен")); }
  }

  const heading = `${index}${title ? ` ${title}` : ""}`;
  return <section className="harness-page" aria-label={`Чертёж жгута ${heading}`}>
    <header className="harness-page-head">
      <button className="secondary small" onClick={onBack}>← Назад</button>
      <h1>{heading}</h1>
      <span className="muted">Чертёж жгута</span>
      {canEdit && <span className={`harness-save tone-${state === "conflict" || state === "error" ? "danger" : state === "saving" ? "warn" : "ok"}`} role="status">{STATE_TEXT[state]}</span>}
      {state === "conflict" && <button className="primary small" onClick={() => setGeneration((value) => value + 1)}>Обновить чертёж</button>}
      <span className="harness-page-actions">
        {note && <span className="muted">{note}</span>}
        <a className="secondary small button-link" href={kitsetsApi.harnessExportUrl(project, index)} download>Скачать для TECHMAP-GRAPHER</a>
        {canEdit && <button className="secondary small" onClick={() => fileRef.current?.click()}>Загрузить .techmap-project.zip</button>}
      </span>
      <input ref={fileRef} type="file" accept=".zip" hidden data-testid="harness-zip" onChange={(event) => { void importZip(event.target.files?.[0]); event.target.value = ""; }} />
    </header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="harness-page-body">
      {!canEdit ? <div className="empty">Чертёж жгута правят сотрудники с правом «Проекты: правка».</div>
        : !api ? <div className="empty">Загрузка…</div>
        : <Suspense fallback={<div className="empty">Загрузка редактора…</div>}>
          <HarnessEditorLazy key={generation} projectId={project} harnessId={index} harnessDesignation={heading} initialView="e4"
            apiOverride={api} catalogOverride={catalog} />
        </Suspense>}
    </div>
  </section>;
}
