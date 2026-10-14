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
  it("показывает только получателя, адрес, телефон и УПД", async () => {
    const dialog = await open();
    expect(within(dialog).getAllByRole("textbox").map((input) => input.getAttribute("aria-label"))).toEqual(["Получатель", "Адрес доставки", "Телефон"]);
    expect(within(dialog).getAllByRole("combobox").map((input) => input.getAttribute("aria-label"))).toEqual(["УПД"]);
    for (const hidden of ["Контактное лицо", "Условия доставки", "Способ отгрузки / ТК", "Плановая дата отгрузки", "Комментарий"]) {
      expect(within(dialog).queryByLabelText(hidden)).not.toBeInTheDocument();
    }
  });

  it("не шлёт скрытые поля: в PUT только версия и изменённое", async () => {
    await open();
    fireEvent.change(screen.getByLabelText("Адрес доставки"), { target: { value: "Тула, ул. Ленина 1" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByText("Данные отправки сохранены");
    expect(calls.find((call) => call.method === "PUT")!.body).toEqual({ project: "591К.3", version: 2, shipping_point: "Тула, ул. Ленина 1" });
  });

  it("«Приложить УПД» — только у бумажной; файл загружается и встаёт в карточку", async () => {
    await open();
    expect(screen.queryByLabelText("Приложить УПД")).not.toBeInTheDocument();
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
});
