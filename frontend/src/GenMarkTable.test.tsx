import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { MarkTable } from "./GenMarkTable";
import { newMarkRow } from "./tcGenMarks";

it("«Вид / материал» — выбор Термотрубка / Хлястик; старое сохранённое значение остаётся в списке строки как есть", () => {
  render(<MarkTable enabled onEnabled={() => undefined} hint="" onChange={() => undefined} targets={[]} kitMarks={[]} comps={[]} mats={[]}
    rows={[newMarkRow({ text: "X1", material: "Термотрубка" }), newMarkRow({ text: "X2", material: "Маркировка" })]} />);

  const fresh = screen.getByLabelText("Материал 1") as HTMLSelectElement;
  expect(within(fresh).getAllByRole("option").map((option) => option.textContent)).toEqual(["Термотрубка", "Хлястик"]);
  const legacy = screen.getByLabelText("Материал 2") as HTMLSelectElement;
  expect(legacy).toHaveValue("Маркировка");
  expect(within(legacy).getAllByRole("option").map((option) => option.textContent)).toEqual(["Термотрубка", "Хлястик", "Маркировка"]);
});

it("маркировочная позиция состава предлагается после Термотрубки и Хлястика и подставляется в новую метку", () => {
  let rows = [newMarkRow({ text: "X1", material: "Бирка TMS" })];
  const { rerender } = render(<MarkTable enabled onEnabled={() => undefined} hint="" onChange={(next) => { rows = next; }} targets={[]} kitMarks={[{ art: "Бирка TMS" }]} comps={[{ art: "Бирка TMS" }]} mats={[]} rows={rows} />);
  expect(within(screen.getByLabelText("Материал 1")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Термотрубка", "Хлястик", "Бирка TMS"]);
  fireEvent.click(screen.getByRole("button", { name: "+ Метка" }));
  expect(rows[1].material).toBe("Бирка TMS");
  rerender(<MarkTable enabled onEnabled={() => undefined} hint="" onChange={(next) => { rows = next; }} targets={[]} kitMarks={[]} comps={[]} mats={[]} rows={[]} />);
  fireEvent.click(screen.getByRole("button", { name: "+ Метка" }));
  expect(rows[0].material).toBe("Термотрубка");
});

it("длинная надпись целиком в поле «Надпись», правка уходит в строку, Enter не ломает значение", () => {
  const long = 'Кабель USB "Сибирь" ТСЦМ.685621.012 для подключения блока питания к модулю управления, длина 1500 мм';
  let rows = [newMarkRow({ text: long })];
  render(<MarkTable enabled onEnabled={() => undefined} hint="" onChange={(next) => { rows = next; }} targets={[]} kitMarks={[]} comps={[]} mats={[]} rows={rows} />);
  const box = screen.getByLabelText("Надпись 1") as HTMLTextAreaElement;
  expect(box.tagName).toBe("TEXTAREA");
  expect(box.value).toBe(long);
  fireEvent.change(box, { target: { value: `${long}, версия 3` } });
  expect(rows[0].text).toBe(`${long}, версия 3`);
  expect(fireEvent.keyDown(box, { key: "Enter" })).toBe(false);
});
