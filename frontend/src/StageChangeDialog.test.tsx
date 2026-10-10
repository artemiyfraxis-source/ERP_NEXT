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

function open(data: StagePreview, apply = vi.fn(async (_reason: string) => {})) {
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
    await waitFor(() => expect(apply).toHaveBeenCalledWith(""));
  });

  it("back: needs a reason before the confirm is enabled", async () => {
    const { apply } = open(preview({ direction: "back", reason_required: true }));
    const confirm = await screen.findByRole("button", { name: "Сменить этап" });
    expect(screen.getByText("Причина (обязательно: этап назад)")).toBeTruthy();
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Причина смены этапа"), { target: { value: "  нажали не туда " } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(apply).toHaveBeenCalledWith("нажали не туда"));
  });

  it("error from the preview is shown and disables the confirm", async () => {
    open(preview({ error: "Проект закрыт — сначала верните в работу.", direction: "none" }));
    expect(await screen.findByText("Проект закрыт — сначала верните в работу.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Сменить этап" })).toBeDisabled();
  });

  it("shows warnings and the plain statement that nothing else changes", async () => {
    open(preview({ warnings: ["Активный блокер «ХОЛД» — смена этапа его не снимает."], kbs: [
      { kb: "КБ1", label: "КБ1", from: "В работе", to: "Отгружено", skipped: [], change: true, note: "", warnings: ["Есть открытые задания цеха: 2"] },
    ] }));
    expect(await screen.findByText("Активный блокер «ХОЛД» — смена этапа его не снимает.")).toBeTruthy();
    expect(screen.getByText("Есть открытые задания цеха: 2")).toBeTruthy();
    expect(screen.getByText(/Меняется только этап/)).toBeTruthy();
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
});
