import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DispatchCard } from "./DispatchCard";

/* «Данные отправки» (владелец 14.10): только получатель, адрес, телефон, вариант УПД; у бумажной — приложить файл. */

const card = {
  project: "591К.3", version: 2, receiver: "ООО Заказчик", shipping_point: "Москва", contact: "Иван", phone: "", terms: "ДОСТАВКА ТК",
  carrier: "СДЭК", upd_type: "ЭДО", updated_by: "", updated_at: null, po: "", contract: "", spec: "", invoice: "", pays: "",
  customer_address: "", hours: "", plan_date: "2026-10-20", note: "Хрупкое", upd_url: "", upd_file_name: "",
};

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let calls: Array<{ url: string; method: string; body: Record<string, unknown> | undefined }> = [];
let stored: Record<string, unknown> = card;

beforeEach(() => {
  calls = [];
  stored = { ...card };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url.startsWith("/api/shipping/project-pack-permissions")) return json({ permissions: [{ item_index: "КБ1", name: "Жгут", quantity: method === "POST" ? (body.lines as Array<{ quantity: string }>)[0].quantity : "0", consumed: "0", remaining: "0", physical_remaining: "10", version: 4 }] });
    if (url.startsWith("/api/shipping/project-addressee?")) return json({ addressee: stored });
    if (url === "/api/shipping/upd-files") return json({ file: { id: "f1", url: "/api/shipping/files/f1", name: body.file_name } });
    if (url === "/api/shipping/project-addressee" && method === "PUT") {
      const { project: _project, version, ...fields } = body;
      stored = { ...stored, ...fields, version: Number(version) + 1, upd_file_name: fields.upd_url ? "УПД 591К.3.pdf" : fields.upd_url === "" ? "" : stored.upd_file_name };
      return json({ addressee: stored });
    }
    return json({});
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const open = async (canEdit = true) => {
  render(<DispatchCard project="591К.3" canEdit={canEdit} close={() => undefined} />);
  await waitFor(() => expect(screen.getByLabelText("Получатель")).toHaveValue("ООО Заказчик"));
  return screen.getByRole("dialog");
};

describe("Данные отправки", () => {
  it("не разрешает запись по выдуманной версии после ошибки загрузки", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json({ detail: "Нет связи" }, 503));
    render(<DispatchCard project="591К.3" stage="accepted" canEdit close={() => undefined} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи");
    expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Получатель")).not.toBeInTheDocument();
  });
  it("до упаковки сохраняет №, дату, файл ЭДО и адрес без создания отправки", async () => {
    render(<DispatchCard project="591К.3" stage="accepted" canEdit close={() => undefined} />);
    fireEvent.change(await screen.findByLabelText("№ УПД"), { target: { value: "523" } });
    fireEvent.change(screen.getByLabelText("Дата УПД"), { target: { value: "2026-11-03" } });
    fireEvent.change(screen.getByLabelText("Адрес доставки"), { target: { value: "Тула" } });
    fireEvent.change(screen.getByLabelText("Получатель"), { target: { value: "ООО Получатель" } });
    fireEvent.change(screen.getByLabelText("Приложить УПД"), { target: { files: [new File(["%PDF-1.4"], "УПД.pdf", { type: "application/pdf" })] } });
    await screen.findByText("УПД приложена");
    expect(screen.getByLabelText("УПД")).toHaveValue("ЭДО");
    expect(screen.getByLabelText("№ УПД")).toHaveValue("523");
    expect(screen.getByLabelText("Адрес доставки")).toHaveValue("Тула");
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByText("Данные отправки сохранены");
    expect(calls.filter((call) => call.method === "PUT").map((call) => call.body)).toEqual([
      { project: "591К.3", version: 2, upd_url: "/api/shipping/files/f1", upd_type: "ЭДО" },
      { project: "591К.3", version: 3, upd_number: "523", upd_date: "2026-11-03", shipping_point: "Тула", receiver: "ООО Получатель" },
    ]);
    expect(calls.some(({ url }) => /project-ship|finished-goods|\/shipments/.test(url))).toBe(false);
  });
  it("номер и дата УПД до упаковки доступны только для просмотра без edit", async () => {
    stored = { ...card, upd_number: "523", upd_date: "2026-11-03" };
    await open(false);
    expect(screen.getByLabelText("№ УПД")).toHaveValue("523");
    expect(screen.getByLabelText("№ УПД")).toBeDisabled();
    expect(screen.getByLabelText("Дата УПД")).toBeDisabled();
    expect(calls.every(({ method }) => method === "GET")).toBe(true);
  });
  it("показывает адресата, доставку и комментарий для склада", async () => {
    const dialog = await open();
    expect(within(dialog).getAllByRole("textbox").map((input) => input.getAttribute("aria-label"))).toEqual(["Получатель", "Адрес доставки", "Телефон", "№ УПД", "Комментарий для склада"]);
    expect(within(dialog).getAllByRole("combobox").map((input) => input.getAttribute("aria-label"))).toEqual(["Тип доставки", "Перевозчик", "УПД"]);
    for (const hidden of ["Контактное лицо", "Условия доставки", "Способ отгрузки / ТК", "Плановая дата отгрузки", "Комментарий"]) {
      expect(within(dialog).queryByLabelText(hidden)).not.toBeInTheDocument();
    }
  });

  it("pickup hides the address, preserves history and saves the warehouse comment with the version", async () => {
    await open();
    fireEvent.change(screen.getByLabelText("Тип доставки"), { target: { value: "САМОВЫВОЗ" } });
    expect(screen.queryByLabelText("Адрес доставки")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Комментарий для склада"), { target: { value: "В понедельник сами приедут" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByText("Данные отправки сохранены");
    expect(calls.find((call) => call.method === "PUT")!.body).toEqual({ project: "591К.3", version: 2, terms: "САМОВЫВОЗ", carrier: "Самовывоз", note: "В понедельник сами приедут" });
    expect(stored.shipping_point).toBe("Москва");
  });

  it("не шлёт скрытые поля: в PUT только версия и изменённое", async () => {
    await open();
    fireEvent.change(screen.getByLabelText("Адрес доставки"), { target: { value: "Тула, ул. Ленина 1" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByText("Данные отправки сохранены");
    expect(calls.find((call) => call.method === "PUT")!.body).toEqual({ project: "591К.3", version: 2, shipping_point: "Тула, ул. Ленина 1" });
  });

  it("файл УПД доступен для ЭДО и бумаги, но не для «Без УПД»", async () => {
    await open();
    expect(screen.getByLabelText("Приложить УПД")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("УПД"), { target: { value: "БЕЗ УПД" } });
    expect(screen.queryByLabelText("Приложить УПД")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("УПД"), { target: { value: "БУМАГА" } });
    expect(screen.getByText("Файл бумажной УПД не приложен")).toBeInTheDocument();
    const input = screen.getByLabelText("Приложить УПД") as HTMLInputElement;
    expect(input.accept).toContain(".pdf");
    fireEvent.change(input, { target: { files: [new File(["%PDF-1.4"], "УПД 591К.3.pdf", { type: "application/pdf" })] } });

    expect(await screen.findByRole("link", { name: "УПД 591К.3.pdf" })).toHaveAttribute("href", "/api/shipping/files/f1");
    const upload = calls.find((call) => call.url === "/api/shipping/upd-files")!;
    expect(upload.body).toMatchObject({ project: "591К.3", file_name: "УПД 591К.3.pdf", content_type: "application/pdf" });
    expect(upload.body).not.toHaveProperty("shipment_id");
    expect(calls.find((call) => call.method === "PUT")!.body).toEqual({ project: "591К.3", version: 2, upd_url: "/api/shipping/files/f1", upd_type: "БУМАГА" });
    expect(screen.getByRole("button", { name: "Заменить" })).toBeInTheDocument();
    expect(screen.getByText("УПД приложена")).toBeInTheDocument();
  });

  it("«Убрать» стирает ссылку на файл с текущей версией", async () => {
    stored = { ...card, upd_type: "БУМАГА", upd_url: "/api/shipping/files/f0", upd_file_name: "старая.pdf" };
    await open();
    expect(screen.getByRole("link", { name: "старая.pdf" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Убрать" }));
    await screen.findByText("Файл бумажной УПД не приложен");
    expect(calls.find((call) => call.method === "PUT")!.body).toEqual({ project: "591К.3", version: 2, upd_url: "" });
  });

  it("только просмотр: файл виден, приложить и убрать нельзя", async () => {
    stored = { ...card, upd_type: "БУМАГА", upd_url: "/api/shipping/files/f0", upd_file_name: "старая.pdf" };
    await open(false);
    expect(screen.getByRole("link", { name: "старая.pdf" })).toBeInTheDocument();
    expect(screen.getByLabelText("Получатель")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Убрать" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Приложить УПД")).not.toBeInTheDocument();
    expect(screen.getByText("Только просмотр")).toBeInTheDocument();
  });

  it("by stage: waiting/production show address fields; ready/accepted add UPD and explicit KB permissions", async () => {
    for (const stage of ["waiting", "production"] as const) {
      const view = render(<DispatchCard project="591К.3" stage={stage} canEdit close={() => undefined} />);
      await waitFor(() => expect(screen.getByLabelText("Получатель")).toHaveValue("ООО Заказчик"));
      expect(screen.getByLabelText("Адрес доставки")).toBeInTheDocument();
      expect(screen.getByLabelText("Телефон")).toBeInTheDocument();
      expect(screen.queryByLabelText("УПД")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("Упаковывать 591К.3")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Разрешить упаковку по КБ" })).not.toBeInTheDocument();
      view.unmount();
    }
    for (const stage of ["ready", "accepted"] as const) {
      const view = render(<DispatchCard project="591К.3" stage={stage} canEdit close={() => undefined} />);
      await waitFor(() => expect(screen.getByLabelText("Получатель")).toHaveValue("ООО Заказчик"));
      expect(screen.getByLabelText("УПД")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Разрешить упаковку по КБ" })).toBeEnabled();
      view.unmount();
    }
  });

  it("packing requires an explicit KB quantity and its own version, not the address version", async () => {
    render(<DispatchCard project="591К.3" stage="ready" canEdit close={() => undefined} />);
    await waitFor(() => expect(screen.getByLabelText("Получатель")).toHaveValue("ООО Заказчик"));
    fireEvent.click(screen.getByRole("button", { name: "Разрешить упаковку по КБ" }));
    const permissions = within(await screen.findByRole("dialog", { name: "Разрешения упаковки 591К.3" }));
    expect(await permissions.findByLabelText("Изменить КБ1")).not.toBeChecked();
    expect(permissions.getByRole("button", { name: "Сохранить разрешения · 0 КБ" })).toBeDisabled();
    fireEvent.change(permissions.getByLabelText("Разрешить ещё КБ1"), { target: { value: "6" } });
    fireEvent.click(permissions.getByRole("button", { name: "Сохранить разрешения · 1 КБ" }));
    await permissions.findByText("Разрешения упаковки сохранены");
    expect(calls.find((call) => call.url === "/api/shipping/project-pack-permissions" && call.method === "POST")?.body).toEqual({ project: "591К.3", lines: [{ item_index: "КБ1", quantity: "6", version: 4 }] });
    expect(calls.some((call) => call.url.endsWith("/project-pack-decision"))).toBe(false);
  });

  it("a packing conflict preserves typed address fields and does not change the independent address version", async () => {
    const onSaved = vi.fn();
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input) === "/api/shipping/project-pack-permissions" && init?.method === "POST") {
        return json({ detail: "Версия изменилась" }, 409);
      }
      return fallback(input, init);
    });
    render(<DispatchCard project="591К.3" stage="accepted" canEdit onSaved={onSaved} close={() => undefined} />);
    await waitFor(() => expect(screen.getByLabelText("Получатель")).toHaveValue(card.receiver));
    fireEvent.change(screen.getByLabelText("Телефон"), { target: { value: "+7 набрано" } });
    fireEvent.click(screen.getByRole("button", { name: "Разрешить упаковку по КБ" }));
    const permissions = within(await screen.findByRole("dialog", { name: "Разрешения упаковки 591К.3" }));
    fireEvent.change(await permissions.findByLabelText("Разрешить ещё КБ1"), { target: { value: "6" } });
    fireEvent.click(permissions.getByRole("button", { name: "Сохранить разрешения · 1 КБ" }));
    await permissions.findByText(/Разрешения изменились/);
    expect(permissions.getByLabelText("Разрешить ещё КБ1")).toHaveValue(6);
    expect(onSaved).not.toHaveBeenCalled();
    fireEvent.click(permissions.getByRole("button", { name: "Закрыть" }));
    expect(screen.getByLabelText("Телефон")).toHaveValue("+7 набрано");
    expect(screen.getByLabelText("Адрес доставки")).toHaveValue("Москва");
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByText("Данные отправки сохранены");
    expect(calls.find((call) => call.method === "PUT")!.body).toEqual({ project: "591К.3", version: 2, phone: "+7 набрано" });
  });
});
