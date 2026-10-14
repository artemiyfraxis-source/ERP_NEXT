import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StageChangeDialog } from "./StageChangeDialog";
import type { StagePreview } from "./api";

const preview = (over: Partial<StagePreview> = {}): StagePreview => ({
  project: "708К.4", value: "Отгружено", kb: "", direction: "forward", reason_required: false, error: "", message: "",
  terminal: "", blocker: "", warnings: [],
  kbs: [
    { kb: "КБ1", label: "КБ1", from: "В работе", to: "Отгружено", skipped: ["КБ на складе"], change: true, note: "", warnings: [] },
    { kb: "КБ2", label: "КБ2", from: "Отгружено", to: "Отгружено", skipped: [], change: false, note: "Выше выбранного этапа — не понижаем", warnings: [] },
  ],
  ...over,
});

function open(data: StagePreview, apply = vi.fn(async (_reason: string, _token: string) => {})) {
  const onClose = vi.fn();
  render(<StageChangeDialog project="708К.4" current="В работе" value="Отгружено" loadPreview={async () => data} apply={apply} onClose={onClose} />);
  return { apply, onClose };
}

describe("StageChangeDialog", () => {
  it("forward: shows from → to, skipped stages and notes, reason optional", async () => {
    const { apply } = open(preview());
    expect(await screen.findByText("пропущены: КБ на складе")).toBeTruthy();
    expect(screen.getByText("В работе → Отгружено")).toBeTruthy();
    expect(screen.getByText("Выше выбранного этапа — не понижаем")).toBeTruthy();
    expect(screen.getByText("Причина (необязательно)")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Сменить этап" }));
    await waitFor(() => expect(apply).toHaveBeenCalledWith("", ""));
  });

  it("back: needs a reason before the confirm is enabled", async () => {
    const { apply } = open(preview({ direction: "back", reason_required: true }));
    const confirm = await screen.findByRole("button", { name: "Сменить этап" });
    expect(screen.getByText("Причина (обязательно: этап назад)")).toBeTruthy();
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Причина смены этапа"), { target: { value: "  нажали не туда " } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(apply).toHaveBeenCalledWith("нажали не туда", ""));
  });

  it("error from the preview is shown and disables the confirm", async () => {
    open(preview({ error: "Проект закрыт — сначала верните в работу.", direction: "none" }));
    expect(await screen.findByText("Проект закрыт — сначала верните в работу.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Сменить этап" })).toBeDisabled();
  });

  it("shows warnings and says there is nothing to close", async () => {
    open(preview({ warnings: ["Активный блокер «ХОЛД» — смена этапа его не снимает."], kbs: [
      { kb: "КБ1", label: "КБ1", from: "В работе", to: "Отгружено", skipped: [], change: true, note: "", warnings: ["Есть открытые задания цеха: 2"] },
    ] }));
    expect(await screen.findByText("Активный блокер «ХОЛД» — смена этапа его не снимает.")).toBeTruthy();
    expect(screen.getByText("Есть открытые задания цеха: 2")).toBeTruthy();
    expect(screen.getByText("Меняется только этап, остальное не трогаем.")).toBeTruthy();
  });

  it("server refusal on apply stays in the dialog and reloads the preview", async () => {
    const load = vi.fn(async () => preview());
    const apply = vi.fn(async () => { throw new Error("Этапы проекта только что изменили — обновите и повторите."); });
    render(<StageChangeDialog project="708К.4" current="В работе" value="Отгружено" loadPreview={load} apply={apply} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Сменить этап" }));
    expect(await screen.findByText("Этапы проекта только что изменили — обновите и повторите.")).toBeTruthy();
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  });

  it("Отмена and Escape close without writing", async () => {
    const { apply, onClose } = open(preview());
    await screen.findByRole("button", { name: "Сменить этап" });
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(apply).not.toHaveBeenCalled();
  });

  const cascade = (): Partial<StagePreview> => ({
    close: [
      { kind: "kit_request", id: "r1", label: "Заявка на комплектацию 0412 · снимается резерв с 9 поз.", qty: "1", unit: "" },
      { kind: "kit_request", id: "r2", label: "Заявка на комплектацию 0413 (довыдача) · снимается резерв с 5 поз.", qty: "1", unit: "" },
      { kind: "task", id: "t1", label: "Задание 77 · Резка", qty: "10", unit: "шт" },
    ],
    keep: [{ kind: "task", label: "Задание 78", reason: "По заданию идёт работа (смена открыта) — остаётся, не мешает переносу." }],
    totals: { tasks: 1, kit_requests: 2, reserves: 0, close: 3, keep: 1 },
    preview_token: "tok-1",
  });

  it("forward with closes: groups with counts and returned reserve, keeps with reasons, token sent", async () => {
    const { apply } = open(preview(cascade()));
    expect(await screen.findByText("Будет закрыто")).toBeTruthy();
    expect(screen.getByText("Заявки на комплектацию: 2")).toBeTruthy();
    expect(screen.getByText("(вернётся резерв 14 поз.)")).toBeTruthy();
    expect(screen.getByText("Задания цеха: 1")).toBeTruthy();
    expect(screen.getByText("Заявка на комплектацию 0412")).toBeTruthy();
    expect(screen.getByText("Остаётся как есть (переносу не мешает): 1")).toBeTruthy();
    expect(screen.getByText(/По заданию идёт работа/)).toBeTruthy();
    expect(screen.queryByText("Меняется только этап, остальное не трогаем.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Сменить этап и закрыть 3" }));
    await waitFor(() => expect(apply).toHaveBeenCalledWith("", "tok-1"));
  });

  it("backward: shows the server note and a plain confirm", async () => {
    open(preview({ direction: "back", reason_required: true, cascade_note: "Назад ничего не открывается", warnings: ["Назад ничего не открывается"] }));
    expect(await screen.findAllByText("Назад ничего не открывается")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Сменить этап" })).toBeTruthy();
    expect(screen.queryByText("Будет закрыто")).toBeNull();
  });

  it("409 on apply with a token: dialog stays, server message shown, preview reloaded with the new token", async () => {
    let n = 0;
    const load = vi.fn(async () => preview({ ...cascade(), preview_token: `tok-${++n}` }));
    const apply = vi.fn(async (_r: string, _t: string) => { throw new Error("Этапы проекта или связанные задания изменились — обновите и повторите"); });
    render(<StageChangeDialog project="708К.4" current="В работе" value="Отгружено" loadPreview={load} apply={apply} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Сменить этап и закрыть 3" }));
    expect(await screen.findByText(/связанные задания изменились/)).toBeTruthy();
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
    expect(apply).toHaveBeenCalledWith("", "tok-1");
    fireEvent.click(await screen.findByRole("button", { name: "Сменить этап и закрыть 3" }));
    await waitFor(() => expect(apply).toHaveBeenLastCalledWith("", "tok-2"));
  });
});
