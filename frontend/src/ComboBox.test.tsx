import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { expect, it } from "vitest";
import { ComboBox } from "./ComboBox";

const UNITS = ["шт", "кг", "м", "л", "уп", "компл"].map((unit) => ({ value: unit, label: unit }));

function Unit({ onValue }: { onValue: (value: string) => void }) {
  const [value, setValue] = useState("");
  return <ComboBox aria-label="Ед." strict options={UNITS} value={value} onChange={(next) => { setValue(next); onValue(next); }} />;
}

it("an exact match wins on Enter: «м» is «м», not the first unit «шт»", () => {
  let picked = "";
  render(<Unit onValue={(value) => { picked = value; }} />);
  const input = screen.getByRole("combobox", { name: "Ед." });
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "м" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(picked).toBe("м");
});

it("leaving the field after an exact match keeps it", () => {
  let picked = "";
  render(<Unit onValue={(value) => { picked = value; }} />);
  const input = screen.getByRole("combobox", { name: "Ед." });
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "кг" } });
  fireEvent.blur(input);
  expect(picked).toBe("кг");
});
