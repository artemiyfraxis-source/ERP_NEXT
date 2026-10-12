import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { ComponentLibrary } from "./ComponentLibrary";
import { createInMemoryComponentLibraryApi, emptyReferences, publishTheirs, TEMPLATE_ID } from "./component-library-fixtures";

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});

describe("ComponentLibrary screen", () => {
  it("новый шаблон: «Опубликовать версию» ждёт код и описание и создаёт версию 1", async () => {
    const api = createInMemoryComponentLibraryApi([]);
    render(<ComponentLibrary api={api} references={emptyReferences} canEdit />);
    fireEvent.click(await screen.findByRole("button", { name: "+ Новый шаблон" }));
    const publish = await screen.findByRole("button", { name: "Опубликовать версию" }) as HTMLButtonElement;
    expect(publish.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Серия соединителя"), { target: { value: "Molex 5557" } });
    expect(publish.disabled).toBe(false);
    fireEvent.click(publish);
    await waitFor(() => expect(api.published).toHaveLength(1));
    expect(api.published[0]).toMatchObject({ templateId: null, baseRevision: null, input: { code: "Molex 5557", name: "Новый компонент", pngBase64: null } });
    expect(api.published[0]!.input.model.schemaVersion).toBe(5);
    await waitFor(() => expect(screen.getByText("Опубликована версия 1")).toBeTruthy());
  });

  it("устаревшая версия: сообщение сервера и «Взять их версию» с вопросом; «Нет» оставляет правки", async () => {
    const api = createInMemoryComponentLibraryApi();
    render(<ComponentLibrary api={api} references={emptyReferences} canEdit />);
    fireEvent.click(await screen.findByRole("button", { name: /JST XH/ }));
    const code = await screen.findByLabelText("Серия соединителя");
    publishTheirs(api, TEMPLATE_ID, "Мастер", { version: 2, name: "Их описание" });
    fireEvent.change(code, { target: { value: "JST XH-2" } });
    fireEvent.click(screen.getByRole("button", { name: "Опубликовать версию" }));
    expect(await screen.findByText("Шаблон уже изменил Мастер — версия 2")).toBeTruthy();
    expect(api.published[0]!.baseRevision).toBe(1);

    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: "Взять их версию" }));
    expect((screen.getByLabelText("Серия соединителя") as HTMLInputElement).value).toBe("JST XH-2");
    fireEvent.click(screen.getByRole("button", { name: "Взять их версию" }));
    await waitFor(() => expect((screen.getByLabelText("Описание серии") as HTMLInputElement).value).toBe("Их описание"));
    expect((screen.getByLabelText("Серия соединителя") as HTMLInputElement).value).toBe("JST XH");
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("только просмотр: правки не доходят до шаблона, публикации нет", async () => {
    const api = createInMemoryComponentLibraryApi();
    render(<ComponentLibrary api={api} references={emptyReferences} canEdit={false} />);
    fireEvent.click(await screen.findByRole("button", { name: /JST XH/ }));
    const code = await screen.findByLabelText("Серия соединителя") as HTMLInputElement;
    fireEvent.change(code, { target: { value: "Другой" } });
    expect(code.value).toBe("JST XH");
    expect(screen.queryByRole("button", { name: "Опубликовать версию" })).toBeNull();
    expect(api.published).toHaveLength(0);
  });
});
