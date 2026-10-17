import { expect, it } from "vitest";
import { calculatedVatRate, formatMoney, formatQuantity } from "./ProcurementSheetColumns";
import type { ProcurementLine } from "./api";

const plain = (text: string) => text.replace(/\s/g, " ");

it("formats money with a thousands gap, comma kopecks and ₽ or the currency code; empty stays empty", () => {
  expect(plain(formatMoney("1250.5"))).toBe("1 250,50 ₽");
  expect(plain(formatMoney(0))).toBe("0,00 ₽");
  expect(plain(formatMoney("0.0325", "RUB"))).toBe("0,0325 ₽");
  expect(plain(formatMoney("99.9", "usd"))).toBe("99,90 USD");
  expect(plain(formatMoney("1234567.891"))).toBe("1 234 567,891 ₽");
  expect(formatMoney(null)).toBe("");
  expect(formatMoney("")).toBe("");
  expect(formatMoney("не число")).toBe("не число");
});

it("writes quantities with a comma", () => {
  expect(formatQuantity("2.5")).toBe("2,5");
  expect(formatQuantity("10")).toBe("10");
  expect(formatQuantity(null)).toBe("");
});

it("derives the calculated VAT rate from the net and gross prices, and says nothing without both", () => {
  const line = (price: string | null, gross: string | null) => ({ calculated_price: price, calculated_price_with_vat: gross }) as ProcurementLine;
  expect(calculatedVatRate(line("100", "122"))).toBe(22);
  expect(calculatedVatRate(line("8.03", "9.8"))).toBe(22);
  expect(calculatedVatRate(line("100", "100"))).toBe(0);
  expect(calculatedVatRate(line("100", null))).toBeNull();
  expect(calculatedVatRate(line("0", "10"))).toBeNull();
});
