import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { AutoTextarea } from "./AutoTextarea";

function mockScroll(height: number) {
  vi.spyOn(Element.prototype, "scrollHeight", "get").mockReturnValue(height);
}
afterEach(() => vi.restoreAllMocks());

it("стартует с одной строки и хранит aria-label и title", () => {
  render(<AutoTextarea value="" onChange={() => undefined} aria-label="Надпись 1" title="Надпись" />);
  const box = screen.getByLabelText("Надпись 1");
  expect(box).toHaveAttribute("rows", "1");
  expect(box).toHaveAttribute("title", "Надпись");
});

it("растёт по содержимому и не выше максимума, дальше прокрутка", () => {
  mockScroll(40);
  const { rerender } = render(<AutoTextarea value="длинный текст" onChange={() => undefined} aria-label="т" maxRows={6} />);
  const box = screen.getByLabelText("т") as HTMLTextAreaElement;
  const small = parseFloat(box.style.height);
  expect(small).toBeGreaterThanOrEqual(40);
  expect(box.style.overflowY).toBe("hidden");
  mockScroll(80);
  rerender(<AutoTextarea value="два ряда" onChange={() => undefined} aria-label="т" maxRows={6} />);
  expect(parseFloat(box.style.height)).toBe(small + 40);
  mockScroll(500);
  rerender(<AutoTextarea value="очень длинный текст" onChange={() => undefined} aria-label="т" maxRows={6} />);
  expect(parseFloat(box.style.height)).toBeLessThan(500);
  expect(box.style.overflowY).toBe("auto");
});

it("onChange отдаёт строку; без multiline перенос превращается в пробел, Enter не вставляет перенос", () => {
  const seen: string[] = [];
  function Host({ multiline }: { multiline?: boolean }) {
    const [value, setValue] = useState("");
    return <AutoTextarea aria-label="т" value={value} multiline={multiline} onChange={(next) => { seen.push(next); setValue(next); }} />;
  }
  const { unmount } = render(<Host />);
  const box = screen.getByLabelText("т") as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: "a\nb" } });
  expect(seen.at(-1)).toBe("a b");
  expect(fireEvent.keyDown(box, { key: "Enter" })).toBe(false);
  unmount();
  render(<Host multiline />);
  const multi = screen.getByLabelText("т");
  fireEvent.change(multi, { target: { value: "a\nb" } });
  expect(seen.at(-1)).toBe("a\nb");
  expect(fireEvent.keyDown(multi, { key: "Enter" })).toBe(true);
});
