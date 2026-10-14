import { expect, it } from "vitest";
import {
  expandMarkSerial, hasMarkSerial, insertMarkSerial, markSerialList, markSerialSpan, markSerialText, markSerialWidth,
  withMarkSerialWidth,
} from "./markSerial";

it("only {№} and {№:1…6} are the product number; anything else stays plain text", () => {
  expect(hasMarkSerial("КБ1-{№}")).toBe(true);
  expect(hasMarkSerial("КБ1-{№:3}")).toBe(true);
  for (const text of ["КБ1-№ 1", "{N}", "{№:0}", "{№:7}", "{№:x}", "{ № }", "", undefined]) expect(hasMarkSerial(text)).toBe(false);
  expect(expandMarkSerial("КБ1-{№:7}", 5)).toBe("КБ1-{№:7}");
});

it("expands every token with its own zero-padding; a number longer than the width is not cut", () => {
  expect(expandMarkSerial("КБ1-{№:3}", 7)).toBe("КБ1-007");
  expect(expandMarkSerial("КБ1-{№}", 7)).toBe("КБ1-7");
  expect(expandMarkSerial("{№:2}/{№}", 3)).toBe("03/3");
  expect(expandMarkSerial("КБ1-{№:2}", 123)).toBe("КБ1-123");
});

it("the width select rewrites all tokens, inserting keeps the width already in the text and goes to the caret", () => {
  expect(markSerialWidth("КБ1-{№:3}")).toBe(3);
  expect(markSerialWidth("КБ1-{№}")).toBe(0);
  expect(withMarkSerialWidth("{№}-{№:2}", 4)).toBe("{№:4}-{№:4}");
  expect(withMarkSerialWidth("КБ1-{№:4}", 0)).toBe("КБ1-{№}");
  expect(insertMarkSerial("КБ1-")).toEqual({ text: "КБ1-{№}", caret: 7 });
  expect(insertMarkSerial("КБ1- изм.2", 4)).toEqual({ text: "КБ1-{№} изм.2", caret: 7 });
  expect(insertMarkSerial("{№:3}/", null)).toEqual({ text: "{№:3}/{№:3}", caret: 11 });
});

it("ETK text: the template with the task range; without a task — «№ по партии»; text without a token is untouched", () => {
  expect(markSerialText("КБ1-{№:3}", { first: 1, last: 10 })).toBe("КБ1-{№} → КБ1-001 … КБ1-010");
  expect(markSerialText("КБ1-{№:3}", { first: 11, last: 12 })).toBe("КБ1-{№} → КБ1-011, КБ1-012");
  expect(markSerialText("КБ1-{№:3}", { first: 4, last: 4 })).toBe("КБ1-{№} → КБ1-004");
  expect(markSerialText("КБ1-{№:3}")).toBe("КБ1-{№} → № по партии");
  expect(markSerialSpan("КБ1-{№}", { first: 1, last: 0 })).toBe("№ по партии");
  expect(markSerialText(" X1 ", { first: 1, last: 10 })).toBe(" X1 ");
});

it("the list of a task has one inscription per item in number order", () => {
  expect(markSerialList("W{№:2}", { first: 9, last: 11 })).toEqual(["W09", "W10", "W11"]);
  expect(markSerialList("W{№}", { first: 3, last: 2 })).toEqual([]);
});
