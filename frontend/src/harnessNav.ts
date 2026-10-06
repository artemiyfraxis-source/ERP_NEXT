// Переходы к чертежу жгута: адрес и открытие/закрытие. Отдельно от HarnessPage, чтобы экран с редактором
// грузился лениво, а карточка проекта знала только про адрес.

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

export function closeHarness(project: string) {
  const url = new URL(window.location.href);
  url.searchParams.delete("harness");
  url.searchParams.set("section", "projects");
  url.searchParams.set("project", project);
  go(`${url.pathname}${url.search}`);
}
