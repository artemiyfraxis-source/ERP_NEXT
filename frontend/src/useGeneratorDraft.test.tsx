import { act, fireEvent, render, screen } from "@testing-library/react";
import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { buildMark, draftKey, GeneratorDraftNote, useGeneratorDraft } from "./useGeneratorDraft";

type Props = { user?: string; index?: string; document?: Record<string, unknown> | null; seed?: string; auto?: string };

/** Форма-стенд: засев при открытии КБ, как в генераторе (layout-эффект), поле ввода и «Создано» — успешная сборка. */
function Form({ user = "u1", index = "КБ1", document = null, seed = "", auto }: Props) {
  const [text, setText] = useState("");
  const value = useMemo(() => ({ text }), [text]);
  const draft = useGeneratorDraft({ user, project: "P-1", index, document, value, apply: (next) => setText(next.text) });
  useLayoutEffect(() => { setText(draft.open({ text: seed }).text); }, [index]); // eslint-disable-line react-hooks/exhaustive-deps
  // Автоподбор после засева (как stickTemplates) — не правка пользователя.
  useEffect(() => { if (auto) setText((current) => current + auto); }, [auto, index]);
  return <div {...draft.bind}>
    <input aria-label="Поле" value={text} onChange={(event) => setText(event.target.value)} />
    <GeneratorDraftNote draft={draft} />
    <button onClick={draft.clear}>Создано</button>
  </div>;
}

const KEY = draftKey("u1", "P-1", "КБ1");
const stored = (key = KEY) => JSON.parse(window.localStorage.getItem(key) ?? "null");
const type = (text: string) => fireEvent.change(screen.getByLabelText("Поле"), { target: { value: text } });

beforeEach(() => { vi.useFakeTimers(); window.localStorage.clear(); });
afterEach(() => { vi.useRealTimers(); window.localStorage.clear(); });

it("saves a second after the last change and shows the time", () => {
  render(<Form />);
  type("А1 XHP");
  expect(screen.getByText("Сохраняю черновик…")).toBeInTheDocument();
  expect(stored()).toBeNull();
  act(() => { vi.advanceTimersByTime(1000); });
  expect(stored()).toMatchObject({ rev: 1, value: { text: "А1 XHP" } });
  expect(screen.getByText(/^Черновик сохранён \d\d:\d\d$/)).toBeInTheDocument();
});

it("keeps what was typed through a tab switch (unmount before the debounce) and restores it with «Отбросить»", () => {
  const first = render(<Form seed="засев" />);
  type("введено");
  first.unmount();
  expect(stored()).toMatchObject({ value: { text: "введено" } });

  render(<Form seed="засев" />);
  expect(screen.getByLabelText("Поле")).toHaveValue("введено");
  expect(screen.getByText(/Восстановлен несохранённый черновик от \d\d\.\d\d\.\d{4}, \d\d:\d\d/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Отбросить" }));
  expect(screen.getByLabelText("Поле")).toHaveValue("засев");
  expect(stored()).toBeNull();
  expect(screen.queryByText(/Восстановлен/)).not.toBeInTheDocument();
  act(() => { vi.advanceTimersByTime(2000); });
  expect(stored()).toBeNull();
});

it("clears the draft after a successful build", () => {
  const first = render(<Form />);
  type("собрано");
  act(() => { vi.advanceTimersByTime(1000); });
  fireEvent.click(screen.getByRole("button", { name: "Создано" }));
  expect(stored()).toBeNull();
  first.unmount();
  expect(stored()).toBeNull();
  render(<Form />);
  expect(screen.getByLabelText("Поле")).toHaveValue("");
  expect(screen.queryByText(/Восстановлен/)).not.toBeInTheDocument();
});

it("does not save the seeding or automatic picks before the user touches the form", () => {
  const first = render(<Form seed="засев" auto="+шаблон" />);
  expect(screen.getByLabelText("Поле")).toHaveValue("засев+шаблон");
  act(() => { vi.advanceTimersByTime(2000); });
  first.unmount();
  expect(stored()).toBeNull();
});

it("drops a draft made before the card was rebuilt", () => {
  const before = { build_config: { ops: ["cutWire"] } };
  const after = { build_config: { ops: ["cutWire", "prsTerm"] } };
  window.localStorage.setItem(KEY, JSON.stringify({ v: 1, rev: 3, savedAt: "2026-10-10T18:00:00Z", built: buildMark(before), value: { text: "старое" } }));
  render(<Form document={after} seed="засев" />);
  expect(screen.getByLabelText("Поле")).toHaveValue("засев");
  expect(stored()).toBeNull();
});

it("restores a draft made over the current build", () => {
  const document = { build_config: { ops: ["cutWire"] } };
  window.localStorage.setItem(KEY, JSON.stringify({ v: 1, rev: 3, savedAt: "2026-10-10T18:58:00Z", built: buildMark(document), value: { text: "черновик" } }));
  render(<Form document={document} />);
  expect(screen.getByLabelText("Поле")).toHaveValue("черновик");
  type("черновик 2");
  act(() => { vi.advanceTimersByTime(1000); });
  expect(stored()).toMatchObject({ rev: 4, value: { text: "черновик 2" } });
});

it("never overwrites a newer draft written by another tab and says so", () => {
  render(<Form />);
  type("моё");
  act(() => { vi.advanceTimersByTime(1000); });
  // Другая вкладка открыла тот же черновик и записала новее.
  window.localStorage.setItem(KEY, JSON.stringify({ ...stored(), rev: 5, value: { text: "из другой вкладки" } }));
  type("моё 2");
  act(() => { vi.advanceTimersByTime(1000); });
  expect(stored()).toMatchObject({ rev: 5, value: { text: "из другой вкладки" } });
  expect(screen.getByText(/Черновик изменён в другой вкладке в \d\d:\d\d — оставлен он/)).toBeInTheDocument();
  type("моё 3");
  act(() => { vi.advanceTimersByTime(1000); });
  expect(stored()).toMatchObject({ rev: 5 });
});

it("keeps drafts apart per КБ and per user, and saves the previous КБ on switching", () => {
  const view = render(<Form />);
  type("КБ1 данные");
  view.rerender(<Form index="КБ2" />);
  expect(stored()).toMatchObject({ value: { text: "КБ1 данные" } });
  expect(screen.getByLabelText("Поле")).toHaveValue("");
  view.rerender(<Form index="КБ1" />);
  expect(screen.getByLabelText("Поле")).toHaveValue("КБ1 данные");
  view.unmount();
  render(<Form user="u2" />);
  expect(screen.getByLabelText("Поле")).toHaveValue("");
});

it("saves at once when the browser tab is hidden", () => {
  render(<Form />);
  type("ухожу");
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  expect(stored()).toMatchObject({ value: { text: "ухожу" } });
});

it("keeps no draft without a user", () => {
  const view = render(<Form user="" />);
  type("аноним");
  view.unmount();
  expect(window.localStorage.length).toBe(0);
});

it("reports a storage failure instead of failing silently", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("QuotaExceededError"); });
  render(<Form />);
  type("много");
  act(() => { vi.advanceTimersByTime(1000); });
  expect(screen.getByText("Черновик не сохранён: хранилище браузера недоступно.")).toBeInTheDocument();
  vi.restoreAllMocks();
});
