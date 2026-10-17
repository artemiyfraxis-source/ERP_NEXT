import { useCallback, useMemo, useRef } from "react";
import { ComponentLibrary } from "./harness/component-library/ComponentLibrary";
import { createComponentLibraryApi, type ComponentLibraryApi } from "./harness/component-library/component-library-api";
import { createErpReferenceCatalog } from "./harness/erpCatalog";
import type { ReferenceCatalogAdapter } from "./harness/reference-catalog";
import { closeLibrary } from "./harnessNav";

/** «Библиотека компонентов» на весь экран: шаблоны разъёмов жгута, их редактор и «Опубликовать версию». Открывается
 *  из чертежа жгута позиции. Без права «Проекты: правка» шаблоны только читаются. Уход со страницы с неопубликованными
 *  правками спрашивает: черновики нигде не хранятся. */
export function ComponentLibraryPage({ project, index, canEdit, onBack = () => closeLibrary(project, index), api, references }: {
  project: string; index: string; canEdit: boolean; onBack?: () => void;
  /** Подмена сети для тестов. */
  api?: ComponentLibraryApi; references?: ReferenceCatalogAdapter;
}) {
  const libraryApi = useMemo(() => api ?? createComponentLibraryApi(), [api]);
  const referenceCatalog = useMemo(() => references ?? createErpReferenceCatalog(), [references]);
  const guard = useRef<(() => Promise<boolean>) | null>(null);
  const setGuard = useCallback((next: (() => Promise<boolean>) | null) => { guard.current = next; }, []);

  async function back() {
    if (guard.current && !await guard.current()) return;
    onBack();
  }

  return <section className="component-library-page" aria-label="Библиотека компонентов">
    <header className="library-page-head">
      <button className="secondary small" onClick={() => void back()}>← К чертежу {index}</button>
      <h1>Библиотека компонентов</h1>
      <span className="muted">Шаблоны разъёмов жгута</span>
    </header>
    <div className="library-page-body">
      <ComponentLibrary api={libraryApi} references={referenceCatalog} canEdit={canEdit} onNavigationGuard={setGuard} />
    </div>
  </section>;
}
