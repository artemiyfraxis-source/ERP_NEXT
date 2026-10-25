import { describe, expect, it } from "vitest";
import { awgToMm2, compareNames, compareNumbers, ethernetCategoryRank, firstNumber } from "./refSort";

describe("порядок по умолчанию в базе данных", () => {
  it("число с десятичной запятой, пустые в конце", () => {
    const sizes = ["1,5", "0,35", "", "10", "0,5", "2,5"];
    const sorted = [...sizes].sort((a, b) => compareNumbers(firstNumber(a), firstNumber(b)));
    expect(sorted).toEqual(["0,35", "0,5", "1,5", "2,5", "10", ""]);
  });
  it("число жил — число перед «x»", () => {
    expect(firstNumber("10x0,5")).toBe(10);
    expect(firstNumber("2x0,5")).toBe(2);
    expect(firstNumber("нет")).toBeNull();
  });
  it("наименование: числа как числа, пустое в конце", () => {
    const names = ["МКШ 10x0,5", "", "МКШ 2x0,5", "МКШ 4x0,5"];
    expect([...names].sort(compareNames)).toEqual(["МКШ 2x0,5", "МКШ 4x0,5", "МКШ 10x0,5", ""]);
  });
  it("категории Ethernet по порядку", () => {
    const names = ["Cat8", "Cat7A", "Cat6a", "Cat6", "Cat5e", "Cat5", "Cat7", "F/UTP"];
    const sorted = [...names].sort((a, b) => compareNumbers(ethernetCategoryRank(a), ethernetCategoryRank(b)));
    expect(sorted).toEqual(["Cat5", "Cat5e", "Cat6", "Cat6a", "Cat7", "Cat7A", "Cat8", "F/UTP"]);
  });
  it("AWG в мм²", () => {
    expect(awgToMm2(24)).toBeCloseTo(0.205, 2);
    expect(awgToMm2(20)).toBeGreaterThan(awgToMm2(24));
  });
});
