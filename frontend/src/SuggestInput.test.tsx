import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { expect, it } from "vitest";
import { SuggestInput } from "./SuggestInput";

function Field() {
  const [value, setValue] = useState("");
  return <label>Тип<SuggestInput options={["Коаксиальный соединитель", "Кабель", "Соединитель"]} value={value} onChange={setValue} /></label>;
}

it("suggests matching options in its own list and still takes free text", () => {
  render(<Field />);
  const input = screen.getByRole("combobox", { name: "Тип" });
  fireEvent.focus(input);
  expect(screen.getAllByRole("option")).toHaveLength(3);

  fireEvent.change(input, { target: { value: "соед" } });
  expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["Коаксиальный соединитель", "Соединитель"]);
  fireEvent.keyDown(input, { key: "ArrowDown" });
  fireEvent.keyDown(input, { key: "ArrowDown" });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(input).toHaveValue("Соединитель");
  expect(screen.queryByRole("listbox")).toBeNull();

  fireEvent.change(input, { target: { value: "Кабель" } });
  fireEvent.mouseDown(screen.getByRole("option", { name: "Кабель" }));
  expect(input).toHaveValue("Кабель");

  fireEvent.change(input, { target: { value: "Термоусадка" } });
  expect(screen.queryByRole("listbox")).toBeNull();
  fireEvent.blur(input);
  expect(input).toHaveValue("Термоусадка");
});
