import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AssemblyDrafts, mergedDrawingName } from "./AssemblyDrafts";

/* Несколько PDF чертежа позиции → один файл КД (владелец 10.10): список, порядок, имя, один запрос. */
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const pdf = (name: string, text = "%PDF-1.4") => new File([text], name, { type: "application/pdf" });

type Call = { path: string; body: Record<string, unknown> | null };

function mount(respond?: (path: string) => Response | undefined) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    calls.push({ path, body: init?.body ? JSON.parse(String(init.body)) : null });
    return respond?.(path) ?? json({ id: "doc9", name: "x", url: null, pages: [{ file_name: "a.pdf", pages: 2 }, { file_name: "b.pdf", pages: 3 }] }, 201);
  });
  const onUploaded = vi.fn();
  render(<AssemblyDrafts project="P-100" itemIndex="КБ1" items={[]} error="" openId="" builtId="" onOpen={vi.fn()} onChanged={vi.fn()} onUploaded={onUploaded} />);
  return { calls, onUploaded };
}

const choose = (...files: File[]) => fireEvent.change(screen.getByLabelText("Файл чертежа"), { target: { files } });
const names = () => within(screen.getByRole("group", { name: "Файлы к загрузке" })).getAllByRole("listitem").map((item) => item.textContent);

afterEach(() => vi.restoreAllMocks());

it("names the merged file after the first one plus the rest", () => {
  expect(mergedDrawingName("ТСЦМ.685621.047_V00-1 (1).pdf", 3)).toBe("ТСЦМ.685621.047_V00-1 (1)+2.pdf");
  expect(mergedDrawingName("one.pdf", 1)).toBe("one.pdf");
});

it("accepts several files, lists them, reorders and removes before the upload", async () => {
  mount();
  expect(screen.getByLabelText("Файл чертежа")).toHaveAttribute("multiple");
  choose(pdf("a.pdf"), pdf("b.pdf"), pdf("c.pdf"));
  expect(names().map((text) => text?.replace(/\s+/g, " "))).toEqual([expect.stringContaining("1. a.pdf"), expect.stringContaining("2. b.pdf"), expect.stringContaining("3. c.pdf")]);
  expect(screen.getByLabelText("Имя сшитого файла")).toHaveValue("a+2.pdf");
  expect(screen.getByRole("button", { name: "Загрузить (3 файлов → один PDF)" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ниже: a.pdf" }));
  expect(names()[0]).toContain("1. b.pdf");
  expect(screen.getByLabelText("Имя сшитого файла")).toHaveValue("b+2.pdf");
  fireEvent.click(screen.getByRole("button", { name: "Убрать: c.pdf" }));
  expect(names()).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Загрузить (2 файлов → один PDF)" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(screen.queryByRole("group", { name: "Файлы к загрузке" })).toBeNull();
});

it("sends the files in the chosen order in one request with the edited name", async () => {
  const { calls, onUploaded } = mount();
  choose(pdf("a.pdf", "%PDF-A"), pdf("b.pdf", "%PDF-B"));
  fireEvent.click(screen.getByRole("button", { name: "Выше: b.pdf" }));
  fireEvent.change(screen.getByLabelText("Имя сшитого файла"), { target: { value: "Общий.pdf" } });
  fireEvent.click(screen.getByRole("button", { name: "Загрузить (2 файлов → один PDF)" }));
  expect(await screen.findByText(/сшит из 2 файлов \(5 стр\.\)/)).toBeInTheDocument();
  expect(calls).toHaveLength(1);
  expect(calls[0].path).toBe("/api/techcards/assembly/drawings/merge");
  expect(calls[0].body).toEqual({
    project: "P-100", item_index: "КБ1", file_name: "Общий.pdf",
    files: [{ file_name: "b.pdf", content_base64: btoa("%PDF-B") }, { file_name: "a.pdf", content_base64: btoa("%PDF-A") }],
  });
  expect(onUploaded).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("group", { name: "Файлы к загрузке" })).toBeNull();
});

it("keeps the list and shows the server message when a part is refused", async () => {
  const { onUploaded } = mount(() => json({ detail: "Файл «b.pdf» повреждён и не читается как PDF" }, 422));
  choose(pdf("a.pdf"), pdf("b.pdf"));
  fireEvent.click(screen.getByRole("button", { name: "Загрузить (2 файлов → один PDF)" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Файл «b.pdf» повреждён");
  expect(names()).toHaveLength(2);
  expect(onUploaded).not.toHaveBeenCalled();
});

it("uploads a single file at once, as before, without the list", async () => {
  const { calls } = mount();
  choose(pdf("KB1.pdf"));
  expect(await screen.findByText("Чертёж «KB1.pdf» загружен в КД позиции")).toBeInTheDocument();
  expect(calls.map((call) => call.path)).toEqual(["/api/techcards/assembly/drawings"]);
  expect(screen.queryByRole("group", { name: "Файлы к загрузке" })).toBeNull();
});

it("refuses to merge a picture with PDFs and takes files dropped on the block", async () => {
  mount();
  choose(pdf("a.pdf"), new File(["x"], "scan.jpg", { type: "image/jpeg" }));
  expect(screen.getByRole("alert")).toHaveTextContent("сшиваются только из PDF — «scan.jpg»");
  expect(screen.queryByRole("group", { name: "Файлы к загрузке" })).toBeNull();
  fireEvent.drop(screen.getByRole("region", { name: "Черновики с чертежей" }), { dataTransfer: { files: [pdf("d1.pdf"), pdf("d2.pdf")] } });
  await waitFor(() => expect(names()).toHaveLength(2));
});
