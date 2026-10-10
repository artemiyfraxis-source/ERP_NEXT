import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EmployeeDocuments } from "./EmployeeDocuments";
import type { DocumentSheet, DocumentsOverview, JournalDocument, OwnDocument } from "./api";

const VERSION = {
  id: "v2", version_no: 2, file_name: "instruction.pdf", size: 1000, sha256: "ab".repeat(32),
  uploaded_by_name: "Кадровик Ирина", uploaded_at: "2026-10-08T09:00:00+03:00",
};

function own(overrides: Partial<OwnDocument> = {}): OwnDocument {
  return {
    id: "d1", title: "Должностная инструкция мастера", kind: "job_description", kind_label: "Должностная инструкция",
    scope: "job_title", scope_label: "Должность", scope_value: "Мастер", audience: "Должность: Мастер", archived: false,
    created_by_name: "Кадровик Ирина", created_at: "2026-10-01T09:00:00+03:00", current: VERSION,
    signed: null, signed_earlier_version: 1, ...overrides,
  };
}

const SIGNED = own({
  id: "d2", title: "Положение об охране труда", kind: "regulation", kind_label: "Положение", audience: "Все сотрудники",
  current: { ...VERSION, id: "v9", version_no: 1 }, signed: { signed_at: "2026-10-09T10:30:00+03:00", method_label: "PIN" },
  signed_earlier_version: null,
});

function journalItem(overrides: Partial<JournalDocument> = {}): JournalDocument {
  return {
    ...own(), signed: 1, readers: 3, versions: [VERSION, { ...VERSION, id: "v1", version_no: 1 }], ...overrides,
  } as JournalDocument;
}

const OVERVIEW: DocumentsOverview = { own: [own(), SIGNED], journal: null, hr: false };

function sheet(overrides: Partial<DocumentSheet> = {}): DocumentSheet {
  return {
    ...journalItem(), version: VERSION, is_current: true, readers: 3, signed: 1,
    rows: [
      { user_id: "u1", name: "Монтажник Анна", department: "Монтаж", job_title: "Мастер", in_scope: true, signed_at: "2026-10-09T10:30:00+03:00", method_label: "PIN" },
      { user_id: "u2", name: "Сидоров Олег", department: "Монтаж", job_title: "Мастер", in_scope: true, signed_at: null, method_label: null },
    ],
    ...overrides,
  } as DocumentSheet;
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Call = { path: string; method: string; body: unknown };

function serve(overview: DocumentsOverview, answer: (call: Call) => Response = () => json(overview)) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const call = { path: String(input), method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null };
    calls.push(call);
    if (call.path === "/api/documents" && call.method === "GET") return json(overview);
    if (call.path.startsWith("/api/documents/d1/sheet")) return json(sheet());
    return answer(call);
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

it("lists what to read with a PDF link and the own history with date and method", async () => {
  const counts: number[] = [];
  serve(OVERVIEW);
  render(<EmployeeDocuments onPending={(count) => counts.push(count)} />);

  const toRead = await screen.findByRole("region", { name: "К ознакомлению" });
  expect(within(toRead).getByText("Должностная инструкция мастера")).toBeInTheDocument();
  expect(within(toRead).getByText("Должность: Мастер")).toBeInTheDocument();
  expect(within(toRead).getByText("08.10.2026 09:00")).toBeInTheDocument();
  expect(within(toRead).getByRole("link", { name: "Открыть PDF" })).toHaveAttribute("href", "/api/documents/versions/v2/file");
  expect(within(toRead).queryByText("Положение об охране труда")).toBeNull();
  await waitFor(() => expect(counts).toEqual([1]));

  const history = screen.getByRole("region", { name: "История ознакомления" });
  expect(within(history).getByText("Положение об охране труда")).toBeInTheDocument();
  expect(within(history).getByText("09.10.2026 10:30")).toBeInTheDocument();
  expect(within(history).getByText("PIN")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Управление документами" })).toBeNull();
  expect(document.body.textContent).not.toMatch(/job_description|2026-10-08/);
});

it("signs «Ознакомлен» with the PIN and shows the document in the history", async () => {
  const after: DocumentsOverview = { ...OVERVIEW, own: [{ ...own(), signed: { signed_at: "2026-10-10T08:00:00+03:00", method_label: "PIN" } }, SIGNED] };
  const counts: number[] = [];
  const calls = serve(OVERVIEW, () => json(after));
  render(<EmployeeDocuments onPending={(count) => counts.push(count)} />);

  const toRead = await screen.findByRole("region", { name: "К ознакомлению" });
  fireEvent.click(within(toRead).getByText("Должностная инструкция мастера"));
  expect(within(toRead).getByText(/Вы подписали редакцию 1/)).toBeInTheDocument();
  const sign = within(toRead).getByRole("button", { name: "Подписать: ознакомлен" });
  expect(sign).toBeDisabled();
  fireEvent.change(within(toRead).getByLabelText("PIN или пароль для подписи"), { target: { value: "482913" } });
  fireEvent.click(sign);

  expect(await screen.findByText("Ознакомлен: «Должностная инструкция мастера».")).toBeInTheDocument();
  expect(calls.filter((call) => call.method === "POST")).toEqual([
    { path: "/api/documents/versions/v2/acknowledge", method: "POST", body: { secret: "482913" } },
  ]);
  expect(screen.getByText("Документов к ознакомлению нет — всё прочитано и подписано.")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "История ознакомления" })).getByText("Должностная инструкция мастера")).toBeInTheDocument();
  expect(counts.at(-1)).toBe(0);
});

it("shows the server's refusal for a wrong PIN and keeps the document unsigned", async () => {
  serve(OVERVIEW, () => json({ detail: "Неверный PIN или пароль — подпись не принята." }, 422));
  render(<EmployeeDocuments />);

  const toRead = await screen.findByRole("region", { name: "К ознакомлению" });
  fireEvent.click(within(toRead).getByText("Должностная инструкция мастера"));
  fireEvent.change(within(toRead).getByLabelText("PIN или пароль для подписи"), { target: { value: "000000" } });
  fireEvent.click(within(toRead).getByRole("button", { name: "Подписать: ознакомлен" }));

  expect(await within(toRead).findByRole("alert")).toHaveTextContent("Неверный PIN или пароль — подпись не принята.");
  expect(within(toRead).getByRole("button", { name: "Подписать: ознакомлен" })).toBeEnabled(); // можно повторить
  expect(screen.queryByText(/Ознакомлен: «/)).toBeNull();
});

it("shows the too-many-attempts refusal (429) as is", async () => {
  serve(OVERVIEW, () => json({ detail: "Слишком много попыток — подождите 15 минут." }, 429));
  render(<EmployeeDocuments />);
  const toRead = await screen.findByRole("region", { name: "К ознакомлению" });
  fireEvent.click(within(toRead).getByText("Должностная инструкция мастера"));
  fireEvent.change(within(toRead).getByLabelText("PIN или пароль для подписи"), { target: { value: "1" } });
  fireEvent.click(within(toRead).getByRole("button", { name: "Подписать: ознакомлен" }));
  expect(await within(toRead).findByRole("alert")).toHaveTextContent("Слишком много попыток — подождите 15 минут.");
});

it("gives an employee without «Кадры» no management", async () => {
  serve(OVERVIEW);
  render(<EmployeeDocuments />);
  await screen.findByRole("region", { name: "К ознакомлению" });
  expect(screen.queryByRole("button", { name: "Новый документ" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Управление документами" })).toBeNull();
});

it("gives «Кадры» the journal, the signature sheet by version and the management actions", async () => {
  const calls = serve({ ...OVERVIEW, hr: true, journal: [journalItem(), journalItem({ id: "d3", title: "Старое положение", archived: true })] },
    () => json(sheet()));
  render(<EmployeeDocuments />);

  const manage = await screen.findByRole("region", { name: "Управление документами" });
  expect(within(manage).getByRole("button", { name: "Новый документ" })).toBeInTheDocument();
  expect(within(manage).getAllByText("1 из 3")).toHaveLength(2);
  expect(within(manage).getByText("В архиве", { selector: ".kit-status" })).toBeInTheDocument();

  fireEvent.click(within(manage).getByText("Должностная инструкция мастера"));
  const signed = await within(manage).findByLabelText("Подписали");
  expect(signed).toHaveTextContent("Монтажник Анна");
  expect(signed).toHaveTextContent("09.10.2026 10:30 · PIN");
  expect(within(manage).getByLabelText("Не подписали")).toHaveTextContent("Сидоров Олег");
  // Один подписал — удалить редакцию нельзя, подсказка называет выход.
  expect(within(manage).getByRole("button", { name: "Удалить последнюю редакцию" })).toBeDisabled();

  fireEvent.change(within(manage).getByRole("combobox"), { target: { value: "1" } });
  await waitFor(() => expect(calls.some((call) => call.path === "/api/documents/d1/sheet?version_no=1")).toBe(true));

  fireEvent.click(within(manage).getByRole("button", { name: "В архив" }));
  await waitFor(() => expect(calls.some((call) => call.path === "/api/documents/d1/archive")).toBe(true));
  expect(calls.find((call) => call.path === "/api/documents/d1/archive")?.body).toEqual({ archived: true });
  expect(await screen.findByText("«Должностная инструкция мастера» отправлен в архив.")).toBeInTheDocument();
});

it("deletes the last unsigned version after a confirmation and shows a 409 as the server wrote it", async () => {
  const unsigned = journalItem({ signed: 0 });
  const calls = serve({ ...OVERVIEW, hr: true, journal: [unsigned] },
    () => json({ detail: "Редакцию уже подписали — удалить её нельзя." }, 409));
  render(<EmployeeDocuments />);

  const manage = await screen.findByRole("region", { name: "Управление документами" });
  fireEvent.click(within(manage).getByText("Должностная инструкция мастера"));
  fireEvent.click(await within(manage).findByRole("button", { name: "Удалить последнюю редакцию" }));
  expect(within(manage).getByText(/Удалить редакцию 2\?/)).toBeInTheDocument();
  fireEvent.click(within(manage).getByRole("button", { name: "Да, удалить" }));

  expect(await within(manage).findByRole("alert")).toHaveTextContent("Редакцию уже подписали — удалить её нельзя.");
  expect(calls.find((call) => call.method === "DELETE")?.path).toBe("/api/documents/versions/v2");
});

it("creates a document: title, kind, scope with its value and the PDF as base64", async () => {
  const calls = serve({ ...OVERVIEW, hr: true, journal: [] }, () => json(sheet(), 201));
  render(<EmployeeDocuments />);

  fireEvent.click(await screen.findByRole("button", { name: "Новый документ" }));
  const form = screen.getByRole("group", { name: "Новый документ" });
  const create = within(form).getByRole("button", { name: "Создать документ" });
  expect(create).toBeDisabled();
  fireEvent.change(within(form).getByLabelText("Название"), { target: { value: "Положение о премировании" } });
  fireEvent.change(within(form).getByLabelText("Вид"), { target: { value: "regulation" } });
  fireEvent.change(within(form).getByLabelText("Для кого"), { target: { value: "department" } });
  fireEvent.change(within(form).getByLabelText("Отдел"), { target: { value: "Монтаж" } });
  fireEvent.change(within(form).getByLabelText("Файл PDF"), { target: { files: [new File(["%PDF-1.4"], "premium.pdf", { type: "application/pdf" })] } });
  await waitFor(() => expect(create).toBeEnabled());
  fireEvent.click(create);

  expect(await screen.findByText("Документ «Положение о премировании» создан — адресаты увидят его в уведомлениях.")).toBeInTheDocument();
  const post = calls.find((call) => call.method === "POST");
  expect(post?.path).toBe("/api/documents");
  expect(post?.body).toEqual({
    title: "Положение о премировании", kind: "regulation", scope: "department", scope_value: "Монтаж",
    file_name: "premium.pdf", content_base64: btoa("%PDF-1.4"),
  });
});
