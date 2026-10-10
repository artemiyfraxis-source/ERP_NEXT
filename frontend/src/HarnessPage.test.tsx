import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { HarnessDesignEditorProps } from "./harness";
import { createEmptyHarnessDesign } from "./harness/editor/model";
import { HarnessPage } from "./HarnessPage";

// Настоящий редактор тяжёлый (его проверяет harness/HarnessEditor.smoke.test): здесь он заменён кнопкой «Правка»,
// которая читает и сохраняет чертёж через переданный адаптер так же, как автосохранение редактора.
vi.mock("./harness", () => ({
  loadHarnessAdapters: async () => ({ createErpHarnessEditorCatalog: () => ({ erp: true, templates: { assetContentUrl: () => "" } }) }),
  HarnessEditorLazy: ({ apiOverride, projectId, harnessId, harnessDesignation, catalogOverride, readOnly, componentPlacementApiOverride }: HarnessDesignEditorProps) => {
    const edit = async () => {
      const resource = await apiOverride!.get(projectId, harnessId);
      await apiOverride!.save(projectId, harnessId, resource.revision, resource.content).catch(() => undefined);
    };
    return <div data-testid="editor" data-read-only={String(Boolean(readOnly))} data-placements={String(Boolean(componentPlacementApiOverride))} data-erp-catalog={String(Boolean((catalogOverride as unknown as { erp?: boolean })?.erp))}><span>{harnessDesignation}</span><button onClick={() => void edit()}>Правка</button></div>;
  },
}));

const design = JSON.parse(JSON.stringify(createEmptyHarnessDesign()));
const drawing = (revision: number) => ({ document: { id: "h1", index: "КБ1", name: "Чертёж", size: 1, stored: true, url: null, uploaded_at: "2026-10-06T10:00:00", who: "Т", kind: "harness" }, design, revision, version: String(revision), warnings: [] });

function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status }); }

describe("HarnessPage", () => {
  let calls: Array<{ url: string; method: string; body?: any }>;
  let putStatus: number;
  beforeEach(() => {
    vi.restoreAllMocks();
    calls = []; putStatus = 200;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = decodeURIComponent(String(input)); const method = init?.method || "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url === "/api/techcards/kitsets/708К.4") return json({ assemblies: [{ index: "КБ1", name: "Жгут питания" }] });
      if (url.endsWith("/harness/КБ1") && method === "GET") return json(drawing(1));
      if (url.endsWith("/harness/КБ1") && method === "PUT") return putStatus === 409
        ? json({ detail: "Чертёж жгута только что сохранили — обновите страницу" }, 409) : json({ ...drawing(2), created: false });
      if (url.endsWith("/harness/КБ1/import")) return json({ ...drawing(3), created: false });
      if (url.endsWith("/harness/КБ1/revisions")) return json({ document_id: "h1", current: 3, total: 3, revisions: [
        { revision: 3, saved_at: "2026-10-10T14:30:00+03:00", saved_by: "Мастер", size: 2048, kind: "restored", restored_from: 1 },
        { revision: 2, saved_at: "2026-10-10T12:00:00+03:00", saved_by: "Технолог", size: 3 * 1024 * 1024, kind: "imported", restored_from: null },
        { revision: 1, saved_at: "2026-10-09T09:15:00+03:00", saved_by: "Технолог", size: 900, kind: "initial", restored_from: null },
      ] });
      if (url.endsWith("/harness/КБ1/revisions/2/restore")) return json(drawing(4));
      return json({});
    }));
  });

  it("шапка: название позиции, «Сохранено», выгрузка и кнопки", async () => {
    render(<HarnessPage project="708К.4" index="КБ1" canEdit />);
    expect(await screen.findByRole("heading", { name: "КБ1 Жгут питания" })).toBeTruthy();
    expect(await screen.findByTestId("editor")).toBeTruthy();
    expect(screen.getByTestId("editor").getAttribute("data-erp-catalog")).toBe("true");
    expect(screen.getByRole("status").textContent).toBe("Сохранено");
    expect(screen.getByRole("link", { name: "Скачать для TECHMAP-GRAPHER" }).getAttribute("href"))
      .toBe("/api/techcards/kitsets/708%D0%9A.4/harness/%D0%9A%D0%911/export");
    expect(screen.getByRole("button", { name: "Загрузить .techmap-project.zip" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "← Назад" })).toBeTruthy();
  });

  it("автосохранение уходит на сервер с версией и возвращает «Сохранено»", async () => {
    render(<HarnessPage project="708К.4" index="КБ1" canEdit />);
    fireEvent.click(await screen.findByRole("button", { name: "Правка" }));
    await waitFor(() => expect(calls.some((call) => call.method === "PUT")).toBe(true));
    expect(calls.find((call) => call.method === "PUT")!.body.revision).toBe(1);
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Сохранено"));
  });

  it("409: «Конфликт», сохранение остановлено, «Обновить чертёж» перечитывает", async () => {
    putStatus = 409;
    render(<HarnessPage project="708К.4" index="КБ1" canEdit />);
    fireEvent.click(await screen.findByRole("button", { name: "Правка" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Конфликт: чертёж изменили — обновить"));
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1);
    putStatus = 200;
    fireEvent.click(screen.getByRole("button", { name: "Обновить чертёж" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Сохранено"));
    expect(screen.queryByRole("button", { name: "Обновить чертёж" })).toBeNull();
  });

  it("загрузка .techmap-project.zip: подтверждение, версия текущего чертежа, перезагрузка редактора", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    render(<HarnessPage project="708К.4" index="КБ1" canEdit />);
    await screen.findByTestId("editor");
    const input = screen.getByTestId("harness-zip");
    const file = new File(["zip"], "a.techmap-project.zip", { type: "application/zip" });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(calls.some((call) => call.url.endsWith("/import"))).toBe(false);
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(calls.some((call) => call.url.endsWith("/import"))).toBe(true));
    expect(calls.find((call) => call.url.endsWith("/import"))!.body).toMatchObject({ file_name: "a.techmap-project.zip", revision: 1 });
    expect(await screen.findByText("Чертёж загружен.")).toBeTruthy();
  });

  it("«История версий»: кто, когда, как появилась; выбранная версия восстанавливается новой, редактор перечитывается", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<HarnessPage project="708К.4" index="КБ1" canEdit />);
    await screen.findByTestId("editor");
    fireEvent.click(screen.getByRole("button", { name: "История версий" }));
    const dialog = await screen.findByRole("dialog", { name: "История версий" });
    expect(await screen.findByText("Восстановлена из версии 1")).toBeTruthy();
    expect(screen.getByText("Загружена из TECHMAP-GRAPHER")).toBeTruthy();
    expect(screen.getByText("Начальная (до истории)")).toBeTruthy();
    expect(screen.getByText("3,0 МБ")).toBeTruthy();
    expect(screen.getByText("текущая")).toBeTruthy();
    expect(dialog.textContent).not.toMatch(/2026-10|restored|imported/);
    const restore = screen.getByRole("button", { name: "Выберите версию" }) as HTMLButtonElement;
    expect(restore.disabled).toBe(true);
    fireEvent.click(screen.getByText("Загружена из TECHMAP-GRAPHER").closest("tr")!);
    fireEvent.click(screen.getByRole("button", { name: "Восстановить версию 2" }));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith("/revisions/2/restore"))).toBe(true));
    expect(calls.find((call) => call.url.endsWith("/revisions/2/restore"))!.body).toEqual({ revision: 3 });
    expect(await screen.findByText("Восстановлена версия 2 — теперь это версия 4.")).toBeTruthy();
  });

  it("без права правки история видна, восстановить нельзя", async () => {
    render(<HarnessPage project="708К.4" index="КБ1" canEdit={false} />);
    await screen.findByTestId("editor");
    fireEvent.click(screen.getByRole("button", { name: "История версий" }));
    expect(await screen.findByText("Восстановлена из версии 1")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Восстановить|Выберите версию/ })).toBeNull();
  });

  it("без права правки чертёж открывается для просмотра: ничего не уходит на сервер", async () => {
    render(<HarnessPage project="708К.4" index="КБ1" canEdit={false} />);
    const editor = await screen.findByTestId("editor");
    expect(editor.getAttribute("data-read-only")).toBe("true");
    expect(screen.getByRole("status").textContent).toBe("Только просмотр");
    expect(screen.queryByRole("button", { name: "Загрузить .techmap-project.zip" })).toBeNull();
    expect(screen.getByRole("link", { name: "Скачать для TECHMAP-GRAPHER" })).toBeTruthy();
    // Даже если редактор попробует сохранить, адаптер просмотра откажет, не дойдя до сервера.
    fireEvent.click(screen.getByRole("button", { name: "Правка" }));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith("/harness/КБ1") && call.method === "GET")).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
  });

  it("с правом правки редактор получает размещения библиотечных компонентов того же чертежа", async () => {
    render(<HarnessPage project="708К.4" index="КБ1" canEdit />);
    const editor = await screen.findByTestId("editor");
    expect(editor.getAttribute("data-read-only")).toBe("false");
    expect(editor.getAttribute("data-placements")).toBe("true");
  });
});
