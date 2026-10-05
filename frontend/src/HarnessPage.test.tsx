import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { HarnessDesignEditorProps } from "./harness";
import { HarnessPage } from "./HarnessPage";

// Настоящий редактор тяжёлый (его проверяет harness/HarnessEditor.smoke.test): здесь он заменён кнопкой «Правка»,
// которая читает и сохраняет чертёж через переданный адаптер так же, как автосохранение редактора.
vi.mock("./harness", () => ({
  HarnessEditorLazy: ({ apiOverride, projectId, harnessId, harnessDesignation }: HarnessDesignEditorProps) => {
    const edit = async () => {
      const resource = await apiOverride!.get(projectId, harnessId);
      await apiOverride!.save(projectId, harnessId, resource.revision, resource.content).catch(() => undefined);
    };
    return <div data-testid="editor"><span>{harnessDesignation}</span><button onClick={() => void edit()}>Правка</button></div>;
  },
}));

const design = { schemaVersion: 1, connectors: [], wires: [], graphics: [], texts: [], dimensions: [] };
const drawing = (version: string) => ({ document: { id: "h1", index: "КБ1", name: "Чертёж", size: 1, stored: true, url: null, uploaded_at: "2026-10-06T10:00:00", who: "Т", kind: "harness" }, design, version, warnings: [] });

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
      if (url.endsWith("/harness/КБ1") && method === "GET") return json(drawing("v1"));
      if (url.endsWith("/harness/КБ1") && method === "PUT") return putStatus === 409
        ? json({ detail: "Чертёж жгута только что сохранили — обновите страницу" }, 409) : json({ ...drawing("v2"), created: false });
      if (url.endsWith("/harness/КБ1/import")) return json({ ...drawing("v3"), created: false });
      return json({});
    }));
  });

  it("шапка: название позиции, «Сохранено», выгрузка и кнопки", async () => {
    render(<HarnessPage project="708К.4" index="КБ1" canEdit />);
    expect(await screen.findByRole("heading", { name: "КБ1 Жгут питания" })).toBeTruthy();
    expect(await screen.findByTestId("editor")).toBeTruthy();
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
    expect(calls.find((call) => call.method === "PUT")!.body.version).toBe("v1");
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
    expect(calls.find((call) => call.url.endsWith("/import"))!.body).toMatchObject({ file_name: "a.techmap-project.zip", version: "v1" });
    expect(await screen.findByText("Чертёж загружен.")).toBeTruthy();
  });

  it("без права правки редактор не открывается", async () => {
    render(<HarnessPage project="708К.4" index="КБ1" canEdit={false} />);
    expect(await screen.findByText(/Чертёж жгута правят/)).toBeTruthy();
    expect(screen.queryByTestId("editor")).toBeNull();
  });
});
