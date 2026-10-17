import { Component, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { failureText, type HarnessDrawing, type HarnessPlacementGraph } from "./api";
import type { GeneratorDrawing } from "./generatorToHarness";
import { HarnessEditorLazy, loadHarnessDrawing, type HarnessDrawingPorts, type HarnessEditorCatalogApi } from "./harness";
import type { ComponentTemplate, ComponentTemplateApi } from "./harness/component-library/component-template-api";
import type { HarnessDesignDocument } from "./harness/editor/model";

/*
 * «Чертёж» в схеме связей генератора (владелец 14.10: «что бы и чертёжик был и схема связей была»): тот же чертёж, что
 * строит «Построить чертёж» (generatorToHarness.ts), но по текущей форме и только для просмотра — в поле редактора жгута
 * без панелей правки, ничего не сохраняется. Пересобирается через полсекунды после правки формы; шаблоны разъёмов
 * библиотеки читаются один раз за сеанс (на каждую правку сеть не дёргается). Сохранить чертёж в проект — по-прежнему
 * кнопкой «Построить / Обновить чертёж».
 */

const DEBOUNCE_MS = 500;

/** Библиотека компонентов с запоминанием: список и версии шаблонов читаются один раз (ошибку не запоминаем). */
let cachedTemplates: ComponentTemplateApi | null = null;
function previewTemplates(base: ComponentTemplateApi): ComponentTemplateApi {
  if (cachedTemplates) return cachedTemplates;
  let list: ReturnType<ComponentTemplateApi["list"]> | null = null;
  const versions = new Map<string, Promise<ComponentTemplate>>();
  cachedTemplates = {
    list() {
      if (!list) list = base.list().catch((reason) => { list = null; throw reason; });
      return list;
    },
    get: (templateId) => base.get(templateId),
    getVersion(templateId, version) {
      const key = `${templateId}@${version}`;
      let found = versions.get(key);
      if (!found) {
        found = base.getVersion(templateId, version).catch((reason) => { versions.delete(key); throw reason; });
        versions.set(key, found);
      }
      return found;
    },
    assetContentUrl: (templateId, version, assetId) => base.assetContentUrl(templateId, version, assetId),
  };
  return cachedTemplates;
}
/** Тесты: библиотека читается заново (каждый тест подменяет ответы API своими). */
export function resetPreviewTemplates() { cachedTemplates = null; }

/** Разъёмы библиотеки на чертеже — как их отдаёт сервер для чертежа, построенного генератором: у разъёма генератора не
 *  UUID («gen-x1»), закреплённого снимка нет — редактор рисует его по своей копии контактов (`unresolved`). */
function placementsOf(document: HarnessDesignDocument): HarnessPlacementGraph {
  const unresolved = document.connectors.filter((connector) => connector.libraryBinding?.mode === "template").map((connector) => connector.designation || connector.id);
  return { placements: [], snapshots: [], unresolved };
}

/** Редактор упал при показе — остаётся сообщение, форма живёт дальше. */
class PreviewBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <div className="error-banner" role="alert">Чертёж не показан: редактор не открылся.</div> : this.props.children; }
}

type Shown = { build: number; ports: HarnessDrawingPorts; catalog: HarnessEditorCatalogApi; drawing: GeneratorDrawing };

/** Чертёж по форме генератора, только просмотр. `stamp` меняется с формой — по нему чертёж пересобирается. */
export function GenDrawingPreview({ stamp, build, project, index }: {
  stamp: string;
  build: (templates: ComponentTemplateApi) => Promise<GeneratorDrawing>;
  project: string;
  index: string;
}) {
  const [shown, setShown] = useState<Shown | null>(null);
  const [state, setState] = useState<{ busy: boolean; problem: string }>({ busy: true, problem: "" });
  const latest = useRef(build);
  latest.current = build;
  const counter = useRef(0);
  useEffect(() => {
    let live = true;
    setState((current) => ({ ...current, busy: true }));
    const timer = window.setTimeout(async () => {
      try {
        const { harnessDrawing, createErpHarnessEditorCatalog } = await loadHarnessDrawing();
        const { libraryTemplates } = await import("./harness/library/library");
        const templates = previewTemplates(libraryTemplates());
        const drawing = await latest.current(templates);
        const graph = placementsOf(drawing.document);
        const design = drawing.document as unknown as Record<string, unknown>;
        const view: HarnessDrawing = { document: null, design, revision: 1 } as HarnessDrawing;
        const ports = harnessDrawing({ templates, readOnly: true, client: {
          drawing: async () => view,
          save: async () => { throw new Error("Чертёж по форме только для просмотра — сохраняет «Построить чертёж»."); },
          placements: async () => graph,
        } });
        if (!live) return;
        counter.current += 1;
        setShown({ build: counter.current, ports, catalog: { ...createErpHarnessEditorCatalog(), templates }, drawing });
        setState({ busy: false, problem: "" });
      } catch (reason) {
        if (live) { setShown(null); setState({ busy: false, problem: failureText(reason, "Чертёж не построен") }); }
      }
    }, DEBOUNCE_MS);
    return () => { live = false; window.clearTimeout(timer); };
  }, [stamp]);

  // Поле вписывается в экран само, но раньше, чем вкладка получает свою высоту: вписать ещё раз, когда редактор встал
  // (кнопкой самого редактора «Вписать в экран» — та же команда, что у человека).
  const frame = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!shown) return;
    const timers: number[] = [];
    let waited = 0;
    const poll = window.setInterval(() => {
      const fit = frame.current?.querySelector<HTMLButtonElement>(".he-fit-tool");
      if (fit) {
        window.clearInterval(poll);
        timers.push(window.setTimeout(() => fit.click(), 250), window.setTimeout(() => fit.isConnected && fit.click(), 1200));
      } else if ((waited += 100) > 10_000) window.clearInterval(poll);
    }, 100);
    return () => { window.clearInterval(poll); timers.forEach((id) => window.clearTimeout(id)); };
  }, [shown]);

  if (state.problem) return <div className="empty">{state.problem}</div>;
  if (!shown) return <div className="empty">Строю чертёж…</div>;
  const warnings = shown.drawing.warnings;
  return <>
    <div className="gen-drawing-state">
      <span className="muted">По форме · в проект не сохранён{state.busy ? " · обновляю…" : ""}</span>
      {warnings.length > 0 && <details><summary>Замечания: {warnings.length}</summary><ul>{warnings.map((item) => <li key={item}>{item}</li>)}</ul></details>}
    </div>
    <div className="gen-drawing-view" role="group" aria-label="Чертёж по форме" ref={frame}>
      <PreviewBoundary key={shown.build}>
        <Suspense fallback={<div className="empty">Загрузка чертежа…</div>}>
          <HarnessEditorLazy key={shown.build} projectId={project} harnessId={index} harnessDesignation={index} initialView="drawing"
            api={shown.ports.api} placements={shown.ports.placements} catalog={shown.catalog} readOnly phoneView />
        </Suspense>
      </PreviewBoundary>
    </div>
  </>;
}
