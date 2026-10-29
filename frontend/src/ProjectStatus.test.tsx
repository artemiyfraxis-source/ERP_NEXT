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
  render(<ProjectStatus project="709К.1" status="В работе" blocker="СТОП" canEdit={false} onChanged={vi.fn()} />);
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.getByText("СТОП")).toBeInTheDocument();
});

it("offers the stages it is given, in that order, instead of its own list", () => {
  const stages = ["Новый", "ОТК", "Принято складом", "Упаковано", "Отгружено"];
  render(<ProjectStatus project="709К.1" status="ОТК" blocker="" canEdit stages={stages} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /Статус проекта 709К.1/ }));
  const items = within(screen.getByRole("menu")).getAllByRole("menuitemradio").map((item) => item.textContent?.trim());
  expect(items.filter((name) => stages.includes(name ?? ""))).toEqual(stages);
  expect(items).not.toContain("КБ на складе");
});

it("falls back to the short ladder, blockers СТОП / ОЖИД ИНФ / ОЖИД КОМПЛ and a marks block", () => {
  render(<ProjectStatus project="709К.1" status="Новый" blocker="" canEdit tcReady kitPicked={false} onChanged={vi.fn()} />);
  expect(screen.getByText("ТК готова")).toBeInTheDocument();
  expect(screen.getByText("КБ не набрана")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Статус проекта 709К.1/ }));
  const menu = screen.getByRole("menu");
  expect(within(menu).getAllByRole("menuitemradio").map((item) => item.textContent?.trim()).filter((name) => !/^(да|нет|авто)$/.test(name ?? "")))
    .toEqual(["Новый", "В работе", "Готово", "ОТК", "ОТК пройден", "Принято складом", "Упаковано", "Отгружено", "Отменён"]);
  expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent?.trim())).toEqual(["Авто", "СТОП", "ОЖИД ИНФ", "ОЖИД КОМПЛ"]);
  expect(within(menu).getByText("Отметки")).toBeInTheDocument();
});

it("picks «Готово» by hand from any stage — backwards from «ОТК» too — through the stage window", async () => {
  // Владелец 29.10: «не могу руками теперь поставить Готово»; «Готово» — производство закончено, перед «ОТК».
  const api: ProjectStatusApi = { stagePreview: vi.fn(async () => ({ ...preview, value: "Готово" })), setStatus: vi.fn(async () => null) };
  const onChanged = vi.fn();
  render(<ProjectStatus project="709К.1" status="ОТК" blocker="" canEdit api={api} onChanged={onChanged} />);
  fireEvent.click(screen.getByRole("button", { name: /Статус проекта 709К.1/ }));
  const ready = within(screen.getByRole("menu")).getByRole("menuitemradio", { name: "Готово" });
  expect(ready).toHaveAttribute("data-tone", "success");
  expect(within(screen.getByRole("menu")).getByRole("menuitemradio", { name: "ОТК пройден" })).toHaveAttribute("data-tone", "success");
  fireEvent.click(ready);
  await waitFor(() => expect(api.stagePreview).toHaveBeenCalledWith("709К.1", "Готово"));
  fireEvent.click(await screen.findByRole("button", { name: "Сменить этап" }));
  await waitFor(() => expect(api.setStatus).toHaveBeenCalledWith("709К.1", { action: "status", value: "Готово", reason: "", preview_token: "tok-1" }));
  expect(onChanged).toHaveBeenCalledWith(null);
});

it("sets a mark by hand and returns it to auto through the adapter", async () => {
  const api: ProjectStatusApi = { stagePreview: vi.fn(async () => preview), setStatus: vi.fn(async () => null) };
  render(<ProjectStatus project="709К.1" status="Новый" blocker="" canEdit tcReady={false} tcManual="нет" api={api} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /Статус проекта 709К.1/ }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: "ТК: авто" }));
  await waitFor(() => expect(api.setStatus).toHaveBeenCalledWith("709К.1", { action: "marker", field: "tc_ready", value: "авто" }));
});
