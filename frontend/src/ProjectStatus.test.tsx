import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ProjectStatus, type ProjectStatusApi } from "./ProjectStatus";

const preview = {
  project: "709К.1", value: "В работе", kb: "", direction: "forward" as const, reason_required: false, error: "", message: "",
  terminal: "", blocker: "", warnings: [], kbs: [], preview_token: "tok-1",
};

it("writes through the given adapter for a project without a card and reports no card back", async () => {
  const api: ProjectStatusApi = {
    stagePreview: vi.fn(async () => preview),
    setStatus: vi.fn(async () => null),
  };
  const onChanged = vi.fn();
  render(<ProjectStatus project="709К.1" status="Новый" blocker="" canEdit api={api} onChanged={onChanged} />);

  fireEvent.click(screen.getByRole("button", { name: /Статус проекта 709К.1/ }));
  const menu = screen.getByRole("menu", { name: "Статус проекта 709К.1" });
  fireEvent.click(within(menu).getByRole("menuitem", { name: "СТОП" }));
  fireEvent.change(screen.getByLabelText("Причина СТОП"), { target: { value: "нет разъёмов" } });
  fireEvent.click(screen.getByRole("button", { name: "Поставить" }));
  await waitFor(() => expect(api.setStatus).toHaveBeenCalledWith("709К.1", { action: "block", value: "СТОП", text: "нет разъёмов" }));
  expect(onChanged).toHaveBeenCalledWith(null);

  fireEvent.click(screen.getByRole("button", { name: /Статус проекта 709К.1/ }));
  fireEvent.click(within(screen.getByRole("menu")).getByRole("menuitemradio", { name: "В работе" }));
  await waitFor(() => expect(api.stagePreview).toHaveBeenCalledWith("709К.1", "В работе"));
  fireEvent.click(await screen.findByRole("button", { name: "Сменить этап" }));
  await waitFor(() => expect(api.setStatus).toHaveBeenCalledWith("709К.1", { action: "status", value: "В работе", reason: "", preview_token: "tok-1" }));
});

it("shows plain chips without the menu when editing is not allowed", () => {
  render(<ProjectStatus project="709К.1" status="В работе" blocker="ХОЛД" canEdit={false} onChanged={vi.fn()} />);
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.getByText("ХОЛД")).toBeInTheDocument();
});

it("offers the stages it is given, in that order, instead of its own list", () => {
  const stages = ["Новый", "ОТК", "Принято складом", "Упаковано", "Отгружено"];
  render(<ProjectStatus project="709К.1" status="ОТК" blocker="" canEdit stages={stages} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /Статус проекта 709К.1/ }));
  const items = within(screen.getByRole("menu")).getAllByRole("menuitemradio").map((item) => item.textContent?.trim());
  expect(items.filter((name) => stages.includes(name ?? ""))).toEqual(stages);
  expect(items).not.toContain("КБ на складе");
});
