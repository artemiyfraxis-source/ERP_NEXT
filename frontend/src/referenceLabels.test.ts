import { expect, it } from "vitest";
import { cleanHeader, headerLabel, valueLabel } from "./referenceLabels";

it("cleans sheet headers: no line breaks, no space before a comma", () => {
  expect(cleanHeader("Шаг\nразьема")).toBe("Шаг разьема");
  expect(cleanHeader("Высота обжима проводника , мм")).toBe("Высота обжима проводника, мм");
});

it("БД-1: English words of the sheet headers become Russian", () => {
  expect(headerLabel("Product Name")).toBe("Наименование");
  expect(headerLabel("Series")).toBe("Серия");
  expect(headerLabel("Артикул\n контакта (REEL)")).toBe("Артикул контакта (катушка)");
  expect(headerLabel("Артикул\n контакта (BAG)")).toBe("Артикул контакта (пакет)");
  expect(headerLabel("Усилие обрыва контакта, N")).toBe("Усилие обрыва контакта, Н");
  expect(headerLabel("Ссылка на DATASHEET на сайте производителя")).toBe("Даташит на сайте производителя");
  expect(headerLabel("Ссылка на datasheet")).toBe("Ссылка на даташит");
  expect(headerLabel("Максисмальная сила тока, А")).toBe("Максимальная сила тока, А");
  expect(headerLabel("Шаг\nразьема")).toBe("Шаг разъёма");
  expect(headerLabel("Наменование")).toBe("Наименование");
});

it("БД-1/БД-5: contact sex and site names are shown the shop's way", () => {
  expect(valueLabel("Тип\n контакта", "Female")).toBe("гнездо");
  expect(valueLabel("Тип контакта", "Female (Socket)")).toBe("гнездо");
  expect(valueLabel("Тип контакта", "Male (Pin)")).toBe("штырь");
  expect(valueLabel("Тип контакта", "Male")).toBe("штырь");
  expect(valueLabel("Производитель", "Female")).toBe("Female"); // только колонка «Тип контакта»
  expect(valueLabel("Расположение", "Цар")).toBe("ЦАР");
  expect(valueLabel("Расположение", "ЖУК")).toBe("ЖУК");
  expect(valueLabel("Расположение", "Стеллаж 3")).toBe("Стеллаж 3");
});
