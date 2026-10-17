import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ComponentLibraryPage } from "./ComponentLibraryPage";
import { emptyReferences, sampleTemplate, TEMPLATE_ID } from "./harness/component-library/component-library-fixtures";

// Экран «Библиотека компонентов» целиком: настоящий клиент api.ts над подменённым fetch, как в HarnessPage.test.

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  // jsdom has no <dialog>.showModal
  HTMLDialogElement.prototype.showModal ||= function showModal(this: HTMLDialogElement) { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close ||= function close(this: HTMLDialogElement) { this.removeAttribute("open"); };
});

function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status }); }

describe("ComponentLibraryPage", () => {
  let calls: Array<{ url: string; method: string; body?: any }>;
  let publishAnswer: (body: any) => Response;

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    calls = [];
    const first = sampleTemplate(1);
    const second = { ...sampleTemplate(2), name: "Разъёмы JST XH (чужая правка)" };
    publishAnswer = body => json({ ...first, version: 2, code: body.code, name: body.name, content: body.model });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = decodeURIComponent(String(input)); const method = init?.method || "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url === "/api/techcards/harness-components") return json([{ ...first, connectorRef: "JST XH", content: undefined }]);
      if (url === `/api/techcards/harness-components/${TEMPLATE_ID}` && method === "GET") return json(calls.filter(call => call.method === "POST").length ? second : first);
      if (url === `/api/techcards/harness-components/${TEMPLATE_ID}/versions` && method === "POST") return publishAnswer(calls.at(-1)!.body);
      if (url === "/api/techcards/harness-catalog") return json({ contractVersion: 1, capturedUtc: "", snapshots: [], counts: {} });
      return json({});
    }));
  });

  async function openTemplate(canEdit: boolean) {
    const view = render(<ComponentLibraryPage project="708К.4" index="КБ1" canEdit={canEdit} onBack={() => undefined} references={emptyReferences} />);
    fireEvent.click(await screen.findByRole("button", { name: /JST XH/ }));
    await screen.findByLabelText("Серия соединителя");
    return view;
  }

  /** Правка контакта: артикул → название контакта в таблице Э4 на экране. */
  async function editContact() {
    const series = await screen.findByRole("button", { name: "Открыть артикул XH-02" });
    fireEvent.click(series);
    const field = await screen.findByLabelText("Назначение, контакт 1");
    fireEvent.change(field, { target: { value: "Питание +5 В" } });
    fireEvent.blur(field);
  }

  it("открыть → править контакт → «Опубликовать версию» отправляет модель с base_revision", async () => {
    await openTemplate(true);
    expect((screen.getByRole("button", { name: "Опубликовать версию" }) as HTMLButtonElement).disabled).toBe(true);
    await editContact();
    expect(screen.getByRole("status", { name: "" }).textContent).toContain("Есть неопубликованные правки");
    fireEvent.click(screen.getByRole("button", { name: "Опубликовать версию" }));
    await waitFor(() => expect(calls.some(call => call.method === "POST")).toBe(true));
    const post = calls.find(call => call.method === "POST")!;
    expect(post.url).toBe(`/api/techcards/harness-components/${TEMPLATE_ID}/versions`);
    expect(post.body.base_revision).toBe(1);
    expect(post.body.code).toBe("JST XH");
    expect(post.body.model.schemaVersion).toBe(5);
    expect(JSON.stringify(post.body.model)).toContain("Питание +5 В");
    await waitFor(() => expect(screen.getByText("Опубликована версия 2")).toBeTruthy());
  });

  it("409: показывает, кто изменил шаблон, и «Взять их версию» открывает чужую версию после вопроса", async () => {
    publishAnswer = () => json({ detail: "Шаблон уже изменил Мастер — версия 2" }, 409);
    await openTemplate(true);
    await editContact();
    fireEvent.click(screen.getByRole("button", { name: "Опубликовать версию" }));
    const banner = await screen.findByText("Шаблон уже изменил Мастер — версия 2");
    expect((screen.getByRole("button", { name: "Опубликовать версию" }) as HTMLButtonElement).disabled).toBe(true);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(within(banner.parentElement!).getByRole("button", { name: "Взять их версию" }));
    await waitFor(() => expect((screen.getByLabelText("Описание серии") as HTMLInputElement).value).toBe("Разъёмы JST XH (чужая правка)"));
    expect(confirm).toHaveBeenCalled();
    expect(screen.queryByText("Шаблон уже изменил Мастер — версия 2")).toBeNull();
  });

  it("422: показывает русскую причину отказа сервера", async () => {
    publishAnswer = () => json({ detail: "Такую модель шаблона библиотека не принимает: схема версии 2" }, 422);
    await openTemplate(true);
    await editContact();
    fireEvent.click(screen.getByRole("button", { name: "Опубликовать версию" }));
    expect(await screen.findByText("Такую модель шаблона библиотека не принимает: схема версии 2")).toBeTruthy();
  });

  it("без права «Проекты: правка» шаблон открывается только для чтения: кнопки публикации нет", async () => {
    await openTemplate(false);
    expect(screen.queryByRole("button", { name: "Опубликовать версию" })).toBeNull();
    expect(screen.queryByRole("button", { name: "+ Новый шаблон" })).toBeNull();
    expect(screen.getByText("Только просмотр")).toBeTruthy();
    expect((screen.getByLabelText("Серия соединителя") as HTMLInputElement).readOnly).toBe(true);
    await screen.findByRole("button", { name: "Открыть артикул XH-02" });
    expect(calls.filter(call => call.method !== "GET")).toHaveLength(0);
  });

  it("уход с неопубликованными правками спрашивает; «Остаться» не уходит, «Отбросить правки» уходит без записи", async () => {
    const back = vi.fn();
    render(<ComponentLibraryPage project="708К.4" index="КБ1" canEdit onBack={back} references={emptyReferences} />);
    fireEvent.click(await screen.findByRole("button", { name: /JST XH/ }));
    await screen.findByLabelText("Серия соединителя");
    // Без правок уходим сразу.
    fireEvent.click(screen.getByRole("button", { name: /К чертежу/ }));
    await waitFor(() => expect(back).toHaveBeenCalledTimes(1));
    await editContact();
    fireEvent.click(screen.getByRole("button", { name: /К чертежу/ }));
    const dialog = await screen.findByRole("heading", { name: "Опубликовать правки шаблона?" });
    fireEvent.click(within(dialog.parentElement!).getByRole("button", { name: "Остаться" }));
    await waitFor(() => expect(back).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: /К чертежу/ }));
    await screen.findByRole("heading", { name: "Опубликовать правки шаблона?" });
    fireEvent.click(screen.getByRole("button", { name: "Отбросить правки" }));
    await waitFor(() => expect(back).toHaveBeenCalledTimes(2));
    expect(calls.filter(call => call.method === "POST")).toHaveLength(0);
    expect(window.localStorage.length).toBe(0);
  });
});
