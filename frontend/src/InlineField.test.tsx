import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EditSection } from "./EditSection";
import { InlineField } from "./InlineField";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("InlineField", () => {
  it("pencil opens the input; Enter saves the typed value, Esc restores without saving", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    render(<InlineField name="phone" label="Телефон" value="+7 900" editable onSave={save} />);
    expect(screen.getByText("+7 900")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Изменить: Телефон" }));
    const input = screen.getByRole("textbox", { name: "Телефон" });
    fireEvent.change(input, { target: { value: "+7 111" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Изменить: Телефон" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Телефон" }), { target: { value: "+7 111" } });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Телефон" }), { key: "Enter" });
    await waitFor(() => expect(save).toHaveBeenCalledWith("+7 111"));
    await waitFor(() => expect(screen.queryByRole("textbox")).not.toBeInTheDocument());
  });

  it("a refusal shows the server words under the field and keeps the typed value", async () => {
    const save = vi.fn().mockRejectedValue(new Error("Отгрузка изменилась после загрузки формы"));
    render(<InlineField name="contact" label="Получатель" value="" editable onSave={save} />);
    expect(screen.getByText("—")).toHaveClass("muted");
    fireEvent.click(screen.getByRole("button", { name: "Изменить: Получатель" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Получатель" }), { target: { value: "Петров" } });
    fireEvent.blur(screen.getByRole("textbox", { name: "Получатель" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Отгрузка изменилась после загрузки формы");
    expect(screen.getByRole("textbox", { name: "Получатель" })).toHaveValue("Петров");
  });

  it("select saves on choice; unchanged value and read-only fields send nothing", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(<InlineField name="terms" label="Способ доставки" kind="select" value="САМОВЫВОЗ"
      options={[["САМОВЫВОЗ", "Самовывоз"], ["ДОСТАВКА ТК", "Доставка ТК"]]} display="Самовывоз" editable onSave={save} />);
    fireEvent.click(screen.getByRole("button", { name: "Изменить: Способ доставки" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Способ доставки" }), { target: { value: "ДОСТАВКА ТК" } });
    await waitFor(() => expect(save).toHaveBeenCalledWith("ДОСТАВКА ТК"));
    rerender(<InlineField name="terms" label="Способ доставки" value="САМОВЫВОЗ" display="Самовывоз" onSave={save} />);
    expect(screen.queryByRole("button", { name: /^Изменить/ })).not.toBeInTheDocument();
  });
});

describe("EditSection", () => {
  const values = { phone: "+7 900", contact: "Иванов" };
  const section = (onSave: (changed: Record<string, string>) => Promise<void>) => render(<EditSection title="Получатель" editable values={values} onSave={onSave}>
    <InlineField name="contact" label="Получатель" value="Иванов" editable onSave={vi.fn()} />
    <InlineField name="phone" label="Телефон" value="+7 900" editable onSave={vi.fn()} />
  </EditSection>);

  it("«Редактировать» opens every field; the unsaved marker appears; «Отмена» restores", () => {
    const save = vi.fn();
    section(save);
    fireEvent.click(screen.getByRole("button", { name: "Редактировать: Получатель" }));
    expect(screen.getAllByRole("textbox")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /^Изменить:/ })).not.toBeInTheDocument();
    expect(screen.queryByText("● не сохранено")).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Телефон" }), { target: { value: "+7 111" } });
    expect(screen.getByText("● не сохранено")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByText("+7 900")).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });

  it("«Сохранить» sends only changed fields; a refusal keeps the block open with the server words", async () => {
    const save = vi.fn().mockRejectedValueOnce(new Error("Организация не может быть пустой")).mockResolvedValueOnce(undefined);
    section(save);
    fireEvent.click(screen.getByRole("button", { name: "Редактировать: Получатель" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Телефон" }), { target: { value: "+7 111" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Организация не может быть пустой");
    expect(screen.getByRole("textbox", { name: "Телефон" })).toHaveValue("+7 111");
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(screen.queryByRole("textbox")).not.toBeInTheDocument());
    expect(save.mock.calls).toEqual([[{ phone: "+7 111" }], [{ phone: "+7 111" }]]);
  });

  it("unsaved changes ask before leaving the page", () => {
    section(vi.fn());
    fireEvent.click(screen.getByRole("button", { name: "Редактировать: Получатель" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Получатель" }), { target: { value: "Петров" } });
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });
});
