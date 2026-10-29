import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { KitsetKbStage, KitsetTile, ProductionOperation, ProductionTask } from "./api";
import { forgetOpenProject, MasterBoard } from "./MasterBoard";
import { boardCard, type BoardCardInput } from "./MasterBoardCard";

// Владелец 29.10: «пересмотреть формат карточек — инфы много, она разная, ООО такое маленькое».
const NOW = new Date("2026-10-29T12:00:00");
const STAGES = ["Новый", "ТК готова", "КБ набрана", "В работе", "ОТК", "Готово к отгрузке", "На отгрузке", "Отгружено", "Отменён"];

function kb(index: string, patch: Partial<KitsetKbStage> = {}): KitsetKbStage {
  return { index, status: "В работе", manual: false, tc: true, kit: true, review: { value: "проверена", text: "", who: "Иванов" }, ...patch };
}

function tile(project: string, patch: Partial<KitsetTile> = {}): KitsetTile {
  return {
    project, customer: "ООО «Радиокомп»", calc_source: "", updated_at: "2026-10-28T14:00:00", updated_by: "Иванов", assemblies: 6, lines: 34,
    kbs: ["КБ1"], status: "В работе", kb_stages: [kb("КБ1")], blocker: "", tc_ready: true, kit_picked: true, ...patch,
  };
}

const noFlags = { late: [], wait: [], over: [] };
function input(patch: Partial<BoardCardInput> = {}): BoardCardInput {
  return { project: "713К.4", tile: tile("713К.4"), statuses: STAGES, taskDeadline: "", free: 0, total: 4, toLaunch: 0, handed: 0, handedSite: "ЖУК", flags: noFlags, now: NOW, ...patch };
}

describe("карточка доски: модель", () => {
  it("один чип: блокер важнее этапа, этапы КБ и отметки — в подсказке", () => {
    const model = boardCard(input({ tile: tile("713К.4", {
      blocker: "СТОП", kit_picked: false, kb_stages: [kb("КБ1"), kb("КБ2", { status: "Упаковано" }), kb("КБ3", { kit: false })],
    }) }));
    expect(model.status.word).toBe("СТОП");
    expect(model.status.title).toBe("Этап проекта: В работе\nБлокер: СТОП\nТК готова · КБ не набрана\nВ работе: КБ1, КБ3\nУпаковано: КБ2");
    expect(boardCard(input()).status).toMatchObject({ word: "В работе" });
    expect(boardCard(input({ tile: undefined })).status.word).toBe("");
  });

  it("проблемы — счётчиками, сначала красные; полные списки — в подсказке", () => {
    const model = boardCard(input({
      tile: tile("713К.4", { tc_ready: false, kit_picked: false, kb_stages: [
        kb("КБ1"), kb("КБ2", { review: null, kit: false }), kb("КБ3", { review: { value: "с погрешностями", text: "", who: "" } }),
        kb("КБ4", { tc: false, review: null, kit: false }),
      ] }),
      flags: { late: ["ПЗ-0001 · КБ1 · 1. Резка"], wait: [], over: [] },
    }));
    expect(model.problems.map((line) => [line.text, line.count, line.tone])).toEqual([
      ["план истёк", 1, "danger"], ["ТК с погрешностями", 1, "danger"],
      ["ТК не выдана", 1, "warning"], ["ТК не проверена", 1, "warning"], ["КБ не набрана", 2, "warning"],
    ]);
    expect(model.problems.find((line) => line.text === "КБ не набрана")?.title).toBe("КБ не набрана: КБ2, КБ4");
    expect(model.problems[0].title).toBe("Не начато, план истёк:\nПЗ-0001 · КБ1 · 1. Резка");
    expect(model.rank).toBe(0);
  });

  it("у отменённого проекта факты про ТК и набор не считаются", () => {
    const model = boardCard(input({ tile: tile("713К.4", { status: "Отменён", kit_picked: false, kb_stages: [kb("КБ1", { kit: false, review: null })] }) }));
    expect(model.problems).toEqual([]);
  });

  it("длинный список операций в подсказке обрезается: 12 строк и «… ещё N»", () => {
    const late = Array.from({ length: 15 }, (_, index) => `ПЗ-${index} · КБ1 · 1. Резка`);
    const title = boardCard(input({ flags: { late, wait: [], over: [] } })).problems[0].title.split("\n");
    expect(title).toHaveLength(14);
    expect(title.at(-1)).toBe("… ещё 3");
  });

  it("строка действия: к запуску, без исполнителя / распределено, отдано на площадку", () => {
    expect(boardCard(input({ toLaunch: 2, free: 3, total: 10, handed: 1 })).actions.map((line) => line.text))
      .toEqual(["к запуску 2", "без исполнителя 3 из 10", "в ЖУК: 1"]);
    expect(boardCard(input()).actions.map((line) => line.text)).toEqual(["распределено 4"]);
  });

  it("срок в шапке: самый горящий из двух, оба — в подсказке; ранг: горит → замечания → чисто", () => {
    const both = boardCard(input({ plan: { deadline: "2026-11-30", days_left: 32 }, taskDeadline: "2026-10-27" }));
    expect(both.deadline).toMatchObject({ text: "задание до 27.10", tone: "danger" });
    expect(both.deadlineTitle).toContain("Дедлайн проекта 30.11.2026");
    expect(both.rank).toBe(0);
    expect(boardCard(input({ plan: { deadline: "2026-11-30", days_left: 32 } })).rank).toBe(2);
    expect(boardCard(input({ tile: tile("713К.4", { kb_stages: [kb("КБ1", { review: null })] }) })).rank).toBe(1);
    expect(boardCard(input()).deadline).toBeNull();
    expect(boardCard(input()).deadlineTitle).toBe("Дедлайна нет в производственном плане");
  });

  it("сборки, позиции и «обновлено» ушли с лица в подсказку шапки", () => {
    expect(boardCard(input()).headTitle).toBe("Заказчик: ООО «Радиокомп»\nсборок 6 · позиций 34\nобновлено 28.10.2026 14:00, Иванов");
  });
});

function op(id: string, patch: Partial<ProductionOperation> = {}): ProductionOperation {
  return {
    id, position: 1, stage: "Резка", code: "", description: "", seconds_per_unit: "1", setup_seconds: "0", norm_seconds: 600, operator_ids: ["p1"],
    planned_start: "2026-10-30T09:00:00", planned_finish: "2026-10-30T10:00:00", assigned_by: null, shares: [],
    required_quantity: "100", fact: { good: "0", defect: "0", processed: "0" }, wip: { ok: "0", closed: "0" }, state: "не начата", ...patch,
  };
}
function task(id: string, project: string, operations: ProductionOperation[]): ProductionTask {
  return {
    id, number: `ПЗ-${id}`, version: 1, created_at: "2026-10-20T08:00:00", project, item: "КБ1", name: "", quantity: 100, fulfillment_request_id: null,
    deadline: null, status: "Запущено", launched_by: "Мастер", handed_to: "", planned_start: null, planned_finish: null, assigned: 0, operations,
  } as ProductionTask;
}

describe("доска мастера: карточки проектов", () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["Date"] }); vi.setSystemTime(NOW); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); forgetOpenProject(); });

  function mount() {
    const tiles = [
      tile("CALM", { customer: "АО «НПП Исток»" }),
      tile("STOP", { blocker: "СТОП", customer: "ООО «Элемент»" }),
      tile("TODO", { kb_stages: [kb("КБ1", { review: null }), kb("КБ2", { review: null })], kbs: ["КБ1", "КБ2"] }),
      tile("SOON"),
    ];
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: tiles, statuses: STAGES }), { status: 200, headers: { "Content-Type": "application/json" } })));
    const tasks = [
      task("1", "CALM", [op("c1")]), task("2", "STOP", [op("s1")]), task("3", "TODO", [op("t1", { operator_ids: [] })]),
      task("4", "SOON", [op("n1"), op("n2", { position: 2, stage: "Опрессовка", planned_start: "2026-10-27T09:00:00", planned_finish: "2026-10-27T10:00:00" })]),
    ];
    return render(<MasterBoard tasks={tasks} operators={[]} skills={[]} absences={[]} canEdit={false} busy="" mutate={async () => {}} openTask={() => {}}
      projectDeadlines={{ calm: { deadline: "2026-11-02", days_left: 4 }, stop: { deadline: "2026-12-20", days_left: 52 }, todo: { deadline: "2026-11-10", days_left: 12 }, soon: { deadline: "2026-12-01", days_left: 33 } }} />);
  }
  const order = () => Array.from(document.querySelectorAll(".mb-card .mb-card-no")).map((node) => node.textContent);

  it("одна карточка — шапка с заказчиком, один статус, строка действия, счётчики; без «сборок» и «обновлено»", async () => {
    mount();
    await waitFor(() => expect(screen.getByText("ООО «Элемент»")).toBeInTheDocument());
    const card = screen.getByText("TODO").closest(".mb-card") as HTMLElement;
    expect(within(card).getByText("ООО «Радиокомп»")).toHaveClass("mb-card-customer");
    expect(card.querySelectorAll(".status-chip")).toHaveLength(1);
    expect(within(card).getByText("без исполнителя 1 из 1")).toHaveAttribute("data-tone", "warning");
    const counter = within(card).getByText(/ТК не проверена/);
    expect(counter.textContent).toBe("ТК не проверена: 2 КБ");
    expect(counter).toHaveAttribute("title", "ТК не проверена: КБ1, КБ2");
    expect(card.textContent).not.toMatch(/сборок|обновлено|Проверка ТК/);
    expect(within(screen.getByText("STOP").closest(".mb-card") as HTMLElement).getByText("СТОП")).toBeInTheDocument();
    expect(within(screen.getByText("SOON").closest(".mb-card") as HTMLElement).getByText(/план истёк/).textContent).toBe("план истёк: 1");
  });

  it("порядок: сначала горящие (блокер, просроченная операция, срок ≤ 3 дн) по сроку, потом с замечаниями, потом чистые", async () => {
    mount();
    await waitFor(() => expect(screen.getByText("ООО «Элемент»")).toBeInTheDocument());
    // CALM: ДЛ через 4 дня — не горит (жёлтый с 3 дн, как во всём приложении), замечаний нет — в конце.
    expect(order()).toEqual(["SOON", "STOP", "TODO", "CALM"]);
  });

  it("клик и Enter по карточке открывают окно проекта; «только нераспределённые» оставляет карточки с операциями без исполнителя", async () => {
    mount();
    await waitFor(() => expect(screen.getByText("ООО «Элемент»")).toBeInTheDocument());
    fireEvent.click(screen.getByText("STOP"));
    expect(screen.getByRole("dialog", { name: "Проект STOP" })).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("dialog", { name: "Проект STOP" })).getByRole("button", { name: /Закрыть/ }));
    fireEvent.keyDown(screen.getByText("CALM").closest(".mb-card") as HTMLElement, { key: "Enter" });
    expect(screen.getByRole("dialog", { name: "Проект CALM" })).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("dialog", { name: "Проект CALM" })).getByRole("button", { name: /Закрыть/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "только нераспределённые" }));
    expect(order()).toEqual(["TODO"]);
  });
});
