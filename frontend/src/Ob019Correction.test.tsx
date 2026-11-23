import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { MachineCommand, MachineControl } from "./machineControlApi";
import { Ob019Correction, correctionPreview } from "./Ob019Correction";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const OFFSETS = (["length_mm", "strip_front_mm", "strip_back_mm", "diameter_mm"] as const).map((key) => ({
  key, label: { length_mm: "Длина провода", strip_front_mm: "Зачистка начала", strip_back_mm: "Зачистка конца", diameter_mm: "Диаметр провода" }[key],
  offset: "0,00", reason: "", updated_by: "", updated_at: null, etk: key.startsWith("strip"),
}));
const CONTROL: MachineControl = { problem: "", state: 102, commands: [], offsets: OFFSETS, can_edit: true };
const MACHINE = { length_mm: 325, strip_front_mm: 5, strip_back_mm: 5, pull_front_mm: 7, pull_back_mm: 0, diameter_mm: 0.8, knife_back_mm: 0.1 };
const LENGTH_COMMAND: MachineCommand = { id: "length-1", status: "queued", status_text: "Ждёт агента станка", tone: "info",
  purpose: "settings", summary: "Поправка длины", fields: [], error: "", created_by: "Слава", created_at: "2026-11-23T10:00:00+03:00", finished_at: null };

function server(answer: unknown = { offset: "5,00", etk: [], written: "325,00", measured: "320,00" }, status = 200) {
  const posts: { url: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    posts.push({ url: decodeURIComponent(String(input)), body: JSON.parse(String(init?.body)) });
    return json(answer, status);
  });
  return posts;
}

afterEach(() => { vi.restoreAllMocks(); });

it("words the correction the server will make from the machine value", () => {
  expect(correctionPreview(325, 320, 325)).toBe("На станке 325,00, получилось 320,00 → поправка +5,00, следующая запись 330,00");
  expect(correctionPreview(5, 5.4, null)).toBe("На станке 5,00, получилось 5,40 → поправка -0,40");
});

it("queues measured length without changing the setpoint and shows D54 only after read-back", async () => {
  const posts = server({ offset: null, etk: [], written: "325,00", measured: "320,00", command: LENGTH_COMMAND });
  const saved = vi.fn(async () => "");
  const { rerender } = render(<Ob019Correction inventory="ОБ.019" control={CONTROL} plan={null} machine={MACHINE} linkUp onSaved={saved} />);
  const what = screen.getByLabelText("Что мерили");
  expect(what).toHaveValue("length_mm");
  // Only what a ruler measures: no diameter, knife or pull-off.
  expect(within(what).getAllByRole("option").map((option) => option.textContent)).toEqual(["Длина провода", "Зачистка начала", "Зачистка конца"]);
  expect(screen.queryByLabelText(/Поправка станка/)).toBeNull();
  fireEvent.change(screen.getByLabelText("Получилось, мм"), { target: { value: "320" } });
  expect(screen.getByText("Задано 325,00 мм, получилось 320,00 мм → станок рассчитает поправку. Длина задания 325,00 мм не изменится")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Ввести поправку вручную/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Записать поправку" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  // The machine value the preview showed goes with it: the server refuses if the machine holds another one now.
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/ОБ.019/correction", body: { parameter: "length_mm", case: "machine", measured: "320", seen: "325,00" } });
  expect(await screen.findByText("Поправка длины: отправлено…")).toBeInTheDocument();
  expect(screen.queryByText(/Поправка записана/)).toBeNull();
  expect(screen.getByRole("button", { name: "Записать поправку" })).toBeDisabled();
  // A later measurement must not be entered under another parameter while this command owns the draft.
  expect(screen.getByLabelText("Что мерили")).toBeDisabled();
  expect(screen.getByLabelText("Получилось, мм")).toBeDisabled();
  expect(screen.getByLabelText("Получилось, мм")).toHaveValue("320");
  expect(saved).toHaveBeenCalledWith("length_mm");
  rerender(<Ob019Correction inventory="ОБ.019" control={{ ...CONTROL, commands: [{ ...LENGTH_COMMAND, status: "done",
    length_correction_before: "0,00", length_correction_after: "-1,25" }], length_correction: { value: "-1,25", register: "D54", legacy_offset: "5,00" } }}
    plan={null} machine={MACHINE} linkUp onSaved={saved} />);
  expect(screen.getByText("Поправка станка: D54 = -1,25 мм")).toBeInTheDocument();
  expect(screen.getByText("Длина провода: старая поправка ERP +5,00 мм — больше не применяется")).toBeInTheDocument();
  expect(screen.getByLabelText("Что мерили")).not.toBeDisabled();
  expect(screen.getByLabelText("Получилось, мм")).toHaveValue("");
  // The bounded machine journal may omit this command after later writes; its known outcome must remain final.
  rerender(<Ob019Correction inventory="ОБ.019" control={{ ...CONTROL, commands: [] }} plan={null} machine={MACHINE} linkUp onSaved={saved} />);
  expect(screen.queryByText("Поправка длины: отправлено…")).toBeNull();
  expect(screen.getByLabelText("Что мерили")).not.toBeDisabled();
  expect(screen.getByText(/Поправка длины: выполнено/)).toBeInTheDocument();
});

it("length needs a live machine and has no manual ERP offset; strip keeps the manual method", async () => {
  const posts = server({ offset: "-0,30", etk: [] });
  render(<Ob019Correction inventory="ОБ.019" control={CONTROL} plan={null} machine={MACHINE} linkUp={false} onSaved={async () => ""} />);
  fireEvent.change(screen.getByLabelText("Получилось, мм"), { target: { value: "320" } });
  expect(screen.getByRole("button", { name: "Записать поправку" })).toBeDisabled();
  expect(screen.getByText("Нужна связь со станком: поправка считается от значения на станке")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Ввести поправку вручную/ })).toBeNull();
  fireEvent.change(screen.getByLabelText("Что мерили"), { target: { value: "strip_front_mm" } });
  fireEvent.click(screen.getByRole("button", { name: /Ввести поправку вручную/ }));
  fireEvent.change(screen.getByLabelText("Поправка станка, мм (со знаком)"), { target: { value: "-0,3" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать поправку" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ parameter: "strip_front_mm", case: "machine", measured: "320", offset: "-0,3" });
  expect(await screen.findByText("Поправка записана: поправка -0,30 мм")).toBeInTheDocument();
});

it("shows the server's refusal next to the button", async () => {
  server({ detail: "Нет свежих данных со станка — повторите, когда станок на связи" }, 422);
  render(<Ob019Correction inventory="ОБ.019" control={CONTROL} plan={null} machine={MACHINE} linkUp onSaved={async () => ""} />);
  fireEvent.change(screen.getByLabelText("Получилось, мм"), { target: { value: "320" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать поправку" }));
  expect(await screen.findByText(/Нет свежих данных со станка/)).toBeInTheDocument();
  expect(screen.getByLabelText("Получилось, мм")).toHaveValue("320");
});

it("keeps the measured length after a runner refusal and does not show a stale D54 as current", async () => {
  server({ offset: null, etk: [], written: "325,00", measured: "320,00", command: LENGTH_COMMAND });
  const control = { ...CONTROL, length_correction: { value: "1,25", register: "D54" as const, legacy_offset: "0,00" } };
  const saved = vi.fn(async () => "");
  const { rerender } = render(<Ob019Correction inventory="ОБ.019" control={control} plan={null} machine={MACHINE} linkUp onSaved={saved} />);
  fireEvent.change(screen.getByLabelText("Получилось, мм"), { target: { value: "320" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать поправку" }));
  await screen.findByText("Поправка длины: отправлено…");
  rerender(<Ob019Correction inventory="ОБ.019" control={{ ...control, commands: [{ ...LENGTH_COMMAND, status: "failed", error: "Станок не готов" }] }}
    plan={null} machine={MACHINE} linkUp={false} onSaved={saved} />);
  expect(screen.getByText("Поправка длины: отказ: Станок не готов")).toBeInTheDocument();
  expect(screen.getByLabelText("Получилось, мм")).toHaveValue("320");
  expect(screen.getByText("Поправка станка: D54 = нет свежих данных")).toBeInTheDocument();
  expect(screen.queryByText(/D54 = 1,25/)).toBeNull();
});

it("a machine value changed after the trial or a write on its way: the 409 shows next to the button", async () => {
  const posts = server({ detail: "Значение на станке изменилось после пробы — проверьте и повторите" }, 409);
  const saved = vi.fn(async () => "");
  render(<Ob019Correction inventory="ОБ.019" control={CONTROL} plan={null} machine={{ ...MACHINE, strip_front_mm: 5.4 }} linkUp onSaved={saved} />);
  fireEvent.change(screen.getByLabelText("Что мерили"), { target: { value: "strip_front_mm" } });
  fireEvent.change(screen.getByLabelText("Получилось, мм"), { target: { value: "4,6" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать поправку" }));
  expect(await screen.findByText("Значение на станке изменилось после пробы — проверьте и повторите")).toHaveClass("tone-danger");
  expect(posts[0].body).toEqual({ parameter: "strip_front_mm", case: "machine", measured: "4,6", seen: "5,40" });
  expect(saved).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Получилось, мм")).toHaveValue("4,6");  // kept for the repeat
});
