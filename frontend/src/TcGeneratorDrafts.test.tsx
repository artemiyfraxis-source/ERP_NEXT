import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { TcGenerator } from "./TcGenerator";
import { PendingLesson } from "./AssemblyDrafts";
import type { AssemblyDraft, KitsetBom, ProjectTechcard } from "./api";

/* «Чертёж → схема» (владелец 05.10): «Поправить вручную» из «Типовой» и «Черновики с чертежей» в ручной сборке. */
const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000001", version: 1, project: "P-100", item_index: "КБ1", product_name: "Жгут XHP 150мм",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-09-23T10:00:00+03:00", updated_at: "2026-09-23T10:00:00+03:00", issued_at: null,
};

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const kit = (): KitsetBom => ({
  project: "P-100", index: "КБ1", name: "Жгут XHP 150мм", run: "1", part_qty: "1", calc_source: "", calc_ops: [],
  components: [
    { id: "kit-0", kd: "", type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 0.3 },
    { id: "kit-1", kd: "", type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: 4 },
    { id: "kit-2", kd: "", type: "Соед", name: "Колодка XH 2", art: "XHP-2", qty: 2 },
  ],
});

/** Ответ POST /api/techcards/assembly/from-generator: типовая сборка как ручная, операции с полями генератора. */
const fromGenerator = () => ({
  mode: "manual", assemblyIndex: "КБ1", assemblyName: "Жгут XHP 150мм", partQty: 1,
  wires: [{ name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 2, length: 160 }], twist: { enabled: false }, bigAssembly: false,
  ops: [
    { type: "cutWireStrip", seq: 1, templateId: "cut", opNum: "CUT_WIRE_auto", tPrep: "60", tOp: "4",
      wires: [{ name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 2, length: 160 }], strip: { a: true, lenA: "5", b: true, lenB: "5" } },
    { type: "prsTerm", seq: 2, side: "A", templateId: "prsA", opNum: "PRS", tPrep: "120", tOp: "6",
      ep: { termArt: "SXH-001T-P0.6", connArt: "XHP-2" }, wires: [{ name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 2, length: 160 }] },
  ],
  route: { blocks: [{ key: "b1", name: "Жгут XHP 150мм", ops: [1, 2], final: true }], edges: [] },
});

const draftRow = (patch: Partial<AssemblyDraft> = {}): AssemblyDraft => ({
  id: "d1", project: "P-100", item_index: "КБ1", drawing_document_id: "doc1", drawing_url: "/api/techcards/kitsets/P-100/documents/doc1",
  source: "claude-code", author: "Claude", status: "draft", final_revision: null, lesson: "", note: "Схема по чертежу КБ1, лист 2",
  created_at: "2026-10-05T10:30:00+03:00", updated_at: "2026-10-05T10:30:00+03:00", ...patch,
});

const draftConfig = () => ({
  mode: "manual", assemblyIndex: "КБ1", assemblyName: "Жгут XHP 150мм", partQty: 1,
  ops: [
    { type: "customOp", seq: 1, label: "Резка по чертежу", qty: "1", tPrep: "60", tOp: "5", steps: [], tooling: [] },
    { type: "customOp", seq: 2, label: "Пайка по чертежу", qty: "1", tPrep: "", tOp: "30", steps: [], tooling: [] },
  ],
  route: { blocks: [{ key: "b1", name: "Заготовка", ops: [1], final: false }, { key: "b2", name: "", ops: [2], final: true }], edges: [{ from: "b1", to: "b2", per_unit: 1 }] },
});

type Calls = Array<{ path: string; method: string; body: Record<string, unknown> | null }>;

function mockApi(drafts: AssemblyDraft[] | "error", extra?: (path: string, init?: RequestInit) => Response | undefined) {
  const calls: Calls = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    calls.push({ path, method: init?.method || "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
    const custom = extra?.(path, init);
    if (custom) return custom;
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
      { id: "prsA", code: "PRS", side: "A", title: "PRS_TERMINAL | Опрессовка терминалов (А)", name: "", category: "Опрессовка терминалов", steps: 1, tooling: 1 },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, columns: [], records: [] });
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "", crimp_height: "", pull_force: "", by_section: [] });
    if (path.startsWith("/api/techcards/assembly/drafts?")) return drafts === "error" ? json({ detail: "Нет доступа" }, 403) : json({ items: drafts });
    throw new Error(`Unexpected request: ${path}`);
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

function mount() {
  const onBuild = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kit()} />);
  return onBuild;
}

const openManual = async () => {
  fireEvent.click(await screen.findByText("Ручная сборка", { selector: "[role=tab]" }, { timeout: 10000 }));
  expect(await screen.findByText("Ручная сборка", { selector: "[role=tab][aria-selected=true]" }, { timeout: 10000 })).toBeInTheDocument();
};

it("«Поправить вручную» opens the typical assembly in the manual tab with blocks and generator operations", async () => {
  const calls = mockApi([], (path, init) => (path === "/api/techcards/assembly/from-generator" && init?.method === "POST" ? json(fromGenerator()) : undefined));
  const onBuild = mount();
  expect((await screen.findAllByText("Резка — МГТФ 0,12"))[0]).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Поправить вручную" }));

  expect(await screen.findByText("Ручная сборка", { selector: "[role=tab][aria-selected=true]" }, { timeout: 10000 })).toBeInTheDocument();
  // На сервер ушла текущая типовая сборка; русские названия операций — как их назвал генератор.
  const request = calls.find((call) => call.path === "/api/techcards/assembly/from-generator")!;
  const sent = request.body!.config as { ops: Array<{ type: string; label?: string }> };
  expect(sent.ops.map((op) => [op.type, op.label])).toEqual([["cutWire", "Резка — МГТФ 0,12"], ["prsTerm", "Опрессовка терминалов А1"], ["prsTerm", "Опрессовка терминалов В1"]]);

  // Собранная сборка открывается схемой, без панели блока; нажатый блок её открывает.
  expect(screen.queryByRole("region", { name: /^Блок «/ })).not.toBeInTheDocument();
  // Итоговая карточка — названа своими операциями, изделие — строкой «итог: …».
  const product = screen.getByRole("article", { name: /^ФИНАЛ: / });
  expect(product.querySelector(".rc-title")).toHaveTextContent("Резка с разделкой · опрессовка терминалов");
  expect(product.querySelector(".rc-sub")).toHaveTextContent("итог: Жгут XHP 150мм");
  fireEvent.keyDown(product, { key: "Enter" });
  // Блок схемы и обе операции; итог назван своими операциями (изделие — строкой «итог: …»), не типами.
  expect(screen.getByRole("region", { name: "Блок «Резка с разделкой · опрессовка терминалов»" })).toBeInTheDocument();
  const first = screen.getByLabelText("Операция 1: Резка с разделкой");
  expect(within(first).getByLabelText("Название операции 1").tagName).toBe("B");
  expect(first).toHaveTextContent("1 пров. · L 160 мм · зачистка А 5, Б 5 мм");
  expect(first).not.toHaveTextContent("cutWireStrip");
  expect(screen.getByLabelText("Операция 2: Опрессовка терминалов")).toHaveTextContent("сторона А · 1 пров. · L 160 мм · терминал SXH-001T-P0.6 · разъём XHP-2");

  // Норма правится, параметров и «на изделие» нет; операцию можно убрать; сборка идёт как обычная ручная.
  fireEvent.click(screen.getByRole("button", { name: /^Развернуть операцию 1:/ }));
  expect(screen.queryByLabelText(/^Параметры:/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/^На изделие:/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Подготовка, мин: Резка с разделкой"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Удалить операцию 2: Опрессовка терминалов" }));
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  const [config, draftId, origin] = onBuild.mock.calls[0];
  expect(draftId).toBeUndefined();
  expect(config).toMatchObject({ mode: "manual", wires: [{ art: "МГТФ 0,12", length: 160 }], twist: { enabled: false } });
  // С ручной сборкой уходит типовая, из которой она сделана (generator_origin): та же форма, что у типовой сборки.
  expect(origin).not.toHaveProperty("mode");
  expect(origin).toMatchObject({ assemblyName: "Жгут XHP 150мм", wires: [{ art: "МГТФ 0,12", length: 150 }] });
  expect(origin.ops.map((op: { type: string; templateId: string }) => [op.type, op.templateId])).toEqual([["cutWire", "cut"], ["prsTerm", "prsA"], ["prsTerm", "prsA"]]);
  expect(config.ops).toHaveLength(1);
  expect(config.ops[0]).toMatchObject({
    type: "cutWireStrip", seq: 1, templateId: "cut", tPrep: "120", tOp: "4", label: "Резка с разделкой",
    wires: [{ length: 160 }], strip: { a: true, lenA: "5", b: true, lenB: "5" },
  });
  expect(config.ops[0]).not.toHaveProperty("qty");
});

it("lists the drafts of the position, opens one into the manual assembly and builds it with draft_id, then saves the lesson", async () => {
  const lessons: unknown[] = [];
  const calls = mockApi([draftRow(), draftRow({ id: "d2", source: "server-agent", status: "final", drawing_url: null, note: "", created_at: "2026-10-04T09:00:00+03:00" })], (path, init) => {
    if (path === "/api/techcards/assembly/drafts/d1" && !init?.method) return json({ ...draftRow(), draft_config: draftConfig() });
    if (path === "/api/techcards/assembly/drafts/d1/lesson" && init?.method === "PUT") {
      lessons.push(JSON.parse(String(init.body)));
      return json(draftRow({ status: "final", final_revision: 2, lesson: "Блок заготовки назвали по-цеховому" }));
    }
    return undefined;
  });
  const onBuild = vi.fn().mockResolvedValue(true);
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kit()} />);
  await openManual();

  const section = await screen.findByRole("region", { name: "Черновики с чертежей" });
  expect(await within(section).findByText("Схема по чертежу КБ1, лист 2")).toBeInTheDocument();
  expect(within(section).getByText("05.10.2026")).toBeInTheDocument();
  expect(within(section).getByText("Claude")).toBeInTheDocument();
  expect(within(section).getByText("агент")).toBeInTheDocument();
  expect(within(section).getByText("черновик")).toBeInTheDocument();
  expect(within(section).getByText("образец")).toBeInTheDocument();
  expect(within(section).getByRole("link", { name: "Чертёж" })).toHaveAttribute("href", "/api/techcards/kitsets/P-100/documents/doc1");
  expect(calls.some((call) => call.path.startsWith("/api/techcards/assembly/drafts?project=P-100&item_index="))).toBe(true);

  fireEvent.click(within(section).getByRole("button", { name: /^Открыть черновик от 05\.10\.2026 10:30/ }));
  expect(await screen.findByLabelText("Название операции 2")).toHaveValue("Пайка по чертежу");
  expect(screen.getByRole("region", { name: "Блок «Пайка по чертежу»" })).toBeInTheDocument();
  expect(blockNames()).toContain("Заготовка");
  expect(within(section).getByRole("button", { name: /^Открыть черновик от 05\.10\.2026 10:30/ })).toHaveTextContent("Открыт");

  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][1]).toBe("d1");
  expect(onBuild.mock.calls[0][0].ops.map((op: { label: string }) => op.label)).toEqual(["Резка по чертежу", "Пайка по чертежу"]);

  // После сборки — «Что поправили и почему».
  const built = await screen.findByRole("group", { name: "Собрано по черновику" });
  fireEvent.change(within(built).getByLabelText("Что поправили и почему"), { target: { value: "Блок заготовки назвали по-цеховому" } });
  fireEvent.click(within(built).getByRole("button", { name: "Сохранить урок" }));
  await waitFor(() => expect(lessons).toEqual([{ lesson: "Блок заготовки назвали по-цеховому" }]));
  expect(await within(built).findByText("Урок сохранён")).toBeInTheDocument();
});

/** Названия блоков схемы. */
function blockNames() {
  return [...document.querySelectorAll("[aria-label=\"Блоки схемы\"] .rc-card")].map((block) => block.textContent || "").join("|");
}

it("does not finalise the draft when the build fails", async () => {
  mockApi([draftRow()], (path, init) => (path === "/api/techcards/assembly/drafts/d1" && !init?.method ? json({ ...draftRow(), draft_config: draftConfig() }) : undefined));
  const onBuild = vi.fn().mockResolvedValue(false);
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kit()} />);
  await openManual();
  fireEvent.click(await screen.findByRole("button", { name: /^Открыть черновик/ }));
  await screen.findByLabelText("Название операции 2");
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(screen.queryByRole("group", { name: "Собрано по черновику" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Открыть черновик/ })).toHaveTextContent("Открыт");
});

it("shows draft against final and the lesson of a finalised draft", async () => {
  const final = draftRow({ id: "d2", status: "final", final_revision: 3, lesson: "Лишний блок убрали", note: "Первый заход" });
  mockApi([final], (path) => (path === "/api/techcards/assembly/drafts/d2"
    ? json({ ...final, draft_config: draftConfig(), final_config: { ...draftConfig(), ops: [draftConfig().ops[0]], route: { blocks: [{ key: "b1", ops: [1], final: true }] } } })
    : undefined));
  mount();
  await openManual();
  fireEvent.click(await screen.findByText("Первый заход"));
  expect(await screen.findByText("2 оп. · 2 блока")).toBeInTheDocument();
  expect(screen.getByText("1 оп. · 1 блок")).toBeInTheDocument();
  expect(screen.getByText("Итог · ревизия 3")).toBeInTheDocument();
  expect(screen.getByLabelText("Что поправили и почему")).toHaveValue("Лишний блок убрали");
});

it("says what to do when the position has no drafts", async () => {
  mockApi([]);
  mount();
  await openManual();
  expect(await screen.findByText("Черновиков по этой позиции нет. Загрузите чертёж (можно несколько PDF — их сошьют в один) и попросите Claude набрать по нему схему: черновик появится здесь.")).toBeInTheDocument();
});

it("uploads a drawing of the position into its КД", async () => {
  const calls = mockApi([], (path, init) => (path === "/api/techcards/assembly/drawings" && init?.method === "POST"
    ? json({ id: "doc9", name: "KB1.pdf", url: "/api/techcards/kitsets/P-100/documents/doc9" }, 201) : undefined));
  mount();
  await openManual();
  await screen.findByText("Черновиков по этой позиции нет. Загрузите чертёж (можно несколько PDF — их сошьют в один) и попросите Claude набрать по нему схему: черновик появится здесь.");
  fireEvent.change(screen.getByLabelText("Файл чертежа"), { target: { files: [new File(["%PDF-1.4"], "KB1.pdf", { type: "application/pdf" })] } });
  expect(await screen.findByText("Чертёж «KB1.pdf» загружен в КД позиции")).toBeInTheDocument();
  const upload = calls.find((call) => call.path === "/api/techcards/assembly/drawings")!;
  expect(upload.body).toEqual({ project: "P-100", item_index: "КБ1", file_name: "KB1.pdf", content_base64: btoa("%PDF-1.4") });
  // Не PDF/JPG/PNG — сразу отказ, на сервер не идёт.
  fireEvent.change(screen.getByLabelText("Файл чертежа"), { target: { files: [new File(["x"], "KB1.dwg")] } });
  expect(await screen.findByRole("alert")).toHaveTextContent("Чертёж принимается в PDF, JPG или PNG.");
  expect(calls.filter((call) => call.path === "/api/techcards/assembly/drawings")).toHaveLength(1);
});

it("keeps the drafts table compact: no percent widths, the open action stays a row button", async () => {
  mockApi([draftRow({ note: "Длинная заметка ".repeat(20) })]);
  mount();
  await openManual();
  const section = await screen.findByRole("region", { name: "Черновики с чертежей" });
  await within(section).findByRole("button", { name: /^Открыть черновик/ });
  const styled = [...section.querySelectorAll<HTMLElement>("th, td, col")].filter((node) => /%/.test(node.style.width));
  expect(styled).toHaveLength(0);
  expect(within(section).getByRole("button", { name: /^Открыть черновик/ })).toHaveTextContent("Открыть");
  expect(section.querySelector(".draft-note-cell")).toHaveAttribute("title", expect.stringContaining("Длинная заметка"));
});

it("keeps the pending lesson across the tab switch and saves it on the ТК tab", async () => {
  const lessons: unknown[] = [];
  mockApi([draftRow()], (path, init) => {
    if (path === "/api/techcards/assembly/drafts/d1" && !init?.method) return json({ ...draftRow(), draft_config: draftConfig() });
    if (path === "/api/techcards/assembly/drafts/d1/lesson" && init?.method === "PUT") {
      lessons.push(JSON.parse(String(init.body)));
      return json(draftRow({ status: "final", lesson: "Поменяли порядок" }));
    }
    return undefined;
  });
  // The position owns the tabs: the generator unmounts on build, the id lives above it.
  function Position() {
    const [tab, setTab] = useState<"gen" | "tc">("gen");
    const [built, setBuilt] = useState("");
    const onBuild = async () => { setTab("tc"); return true; };
    return tab === "gen"
      ? <TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kit()} builtId={built} onBuiltChange={setBuilt} />
      : <div>{built && <PendingLesson draftId={built} />}</div>;
  }
  render(<Position />);
  await openManual();
  fireEvent.click(await screen.findByRole("button", { name: /^Открыть черновик/ }));
  await screen.findByLabelText("Название операции 2");
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));

  const strip = await screen.findByRole("group", { name: "Собрано по черновику" });
  expect(strip).toHaveTextContent("Собрано по черновику от 05.10.2026 — запишите, что поправили");
  fireEvent.change(within(strip).getByLabelText("Что поправили и почему"), { target: { value: "Поменяли порядок" } });
  fireEvent.click(within(strip).getByRole("button", { name: "Сохранить урок" }));
  await waitFor(() => expect(lessons).toEqual([{ lesson: "Поменяли порядок" }]));
});

it("shows the manual-assembly validation error as a banner above the footer, not inside it", async () => {
  mockApi([], (path, init) => (path === "/api/techcards/assembly/from-generator" && init?.method === "POST" ? json(fromGenerator()) : undefined));
  const onBuild = vi.fn().mockRejectedValue(new Error("Не заполнено: название блока"));
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kit()} />);
  fireEvent.click(await screen.findByText("Ручная сборка", { selector: "[role=tab]" }, { timeout: 10000 }));
  fireEvent.click(await screen.findByRole("button", { name: "Создать" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Ручная сборка: добавьте хотя бы одну операцию");
  expect(alert.closest(".gen-foot")).toBeNull();
});
