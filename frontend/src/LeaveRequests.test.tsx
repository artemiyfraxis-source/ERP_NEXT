import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LeaveRequests } from "./LeaveRequests";
import type { LeaveOverview, LeaveRequestView } from "./api";

function view(overrides: Partial<LeaveRequestView> = {}): LeaveRequestView {
  return {
    id: "l1", employee_id: "e1", employee_name: "Монтажник Анна", kind: "vacation", kind_label: "Отпуск",
    from_date: "2026-10-28", to_date: "2026-11-10", days: 14, comment: "По графику", status: "signed",
    status_label: "На согласовании у руководителя", waiting_for: "Мастер Пётр", content_version: 1,
    content_sha256: "ab".repeat(32), version: 2, created_at: "2026-10-14T09:00:00+00:00", updated_at: "2026-10-14T09:00:00+00:00",
    signatures: [{
      step: "employee", step_label: "Сотрудник", decision: "signed", decision_label: "подписал", signer_name: "Монтажник Анна",
      signed_at: "2026-10-14T09:00:00+00:00", method_label: "PIN", content_version: 1, content_sha256: "ab".repeat(32), comment: "",
    }],
    actions: { edit: true, sign: false, decide: false, withdraw: true },
    ...overrides,
  };
}

const OVERVIEW: LeaveOverview = {
  today: "2026-10-14",
  balance: { year: 2026, norm: 28, registered: 0, pending: 14, left: 14 },
  own: [view()],
  inbox: [],
  journal: null,
  hr: false,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Call = { path: string; method: string; body: unknown };

function serve(overview: LeaveOverview, answer: (call: Call) => Response = () => json(view())) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const call = { path: String(input), method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null };
    calls.push(call);
    if (call.path === "/api/leave-requests" && call.method === "GET") return json(overview);
    return answer(call);
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

it("shows the year's norm and the own requests with Russian statuses and dates", async () => {
  serve(OVERVIEW);
  render(<LeaveRequests />);

  const summary = await screen.findByRole("region", { name: "Отпуск 2026" });
  expect(within(summary).getByText("28 дн.")).toBeInTheDocument();
  expect(within(summary).getAllByText("14 дн.")).toHaveLength(2);
  const own = screen.getByRole("region", { name: "Мои заявления" });
  expect(within(own).getByText("28.10.2026 – 10.11.2026")).toBeInTheDocument();
  expect(within(own).getAllByText("На согласовании у руководителя").some((node) => node.classList.contains("kit-status"))).toBe(true);
  expect(within(own).getByText("Мастер Пётр")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "На согласование" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Журнал кадров" })).not.toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/vacation|signed|2026-10-28/);
});

it("files a request: saves the draft, then signs it with the PIN", async () => {
  const calls = serve({ ...OVERVIEW, own: [] }, (call) => {
    if (call.path === "/api/leave-requests" && call.method === "POST") return json(view({ status: "draft", version: 1 }), 201);
    if (call.path === "/api/leave-requests/l1/sign") return json(view({ version: 2 }));
    return json({ detail: "unexpected" }, 500);
  });
  render(<LeaveRequests />);

  fireEvent.click(await screen.findByRole("button", { name: "Новое заявление" }));
  const form = screen.getByRole("region", { name: "Новое заявление" });
  expect(within(form).getByLabelText("Начало")).toHaveValue("2026-10-15"); // завтра — видно и меняется
  fireEvent.click(within(form).getByRole("button", { name: "Отгул" }));
  fireEvent.change(within(form).getByLabelText("Конец"), { target: { value: "2026-10-16" } });
  expect(within(form).getByText("2")).toBeInTheDocument();
  const send = within(form).getByRole("button", { name: "Подписать и отправить" });
  expect(send).toBeDisabled(); // без PIN подписи нет
  fireEvent.change(within(form).getByLabelText("PIN или пароль для подписи"), { target: { value: "482913" } });
  fireEvent.click(send);

  expect(await screen.findByText("Заявление подписано и отправлено: Мастер Пётр.")).toBeInTheDocument();
  const writes = calls.filter((call) => call.method !== "GET");
  expect(writes).toEqual([
    { path: "/api/leave-requests", method: "POST", body: { kind: "day_off", from_date: "2026-10-15", to_date: "2026-10-16", comment: "" } },
    { path: "/api/leave-requests/l1/sign", method: "POST", body: { version: 1, secret: "482913" } },
  ]);
});

it("lets the manager approve or reject from the inbox, a rejection only with a reason", async () => {
  const waiting = view({ actions: { edit: false, sign: false, decide: true, withdraw: false } });
  const counts: number[] = [];
  const calls = serve({ ...OVERVIEW, own: [], inbox: [waiting] }, () => json(view({ status: "rejected", status_label: "Отклонено" })));
  render(<LeaveRequests onWaiting={(count) => counts.push(count)} />);

  const inbox = await screen.findByRole("region", { name: "На согласование" });
  await waitFor(() => expect(counts).toContain(1));
  fireEvent.click(within(inbox).getByText("Монтажник Анна"));
  const reject = within(inbox).getByRole("button", { name: "Отклонить" });
  fireEvent.change(within(inbox).getByLabelText("PIN или пароль для подписи"), { target: { value: "secret-pass" } });
  expect(within(inbox).getByRole("button", { name: "Согласовать" })).toBeEnabled();
  expect(reject).toBeDisabled();
  fireEvent.change(within(inbox).getByLabelText("Комментарий к решению"), { target: { value: "Сдача проекта" } });
  fireEvent.click(reject);

  expect(await screen.findByText("Монтажник Анна: заявление отклонено.")).toBeInTheDocument();
  expect(calls.find((call) => call.method === "POST")).toEqual({
    path: "/api/leave-requests/l1/decision", method: "POST",
    body: { version: 2, approve: false, comment: "Сдача проекта", secret: "secret-pass" },
  });
});

it("shows the signatures with method and version, and the server's refusal in Russian", async () => {
  serve(OVERVIEW, () => json({ detail: "Неверный PIN или пароль — подпись не принята." }, 422));
  render(<LeaveRequests />);

  const own = await screen.findByRole("region", { name: "Мои заявления" });
  fireEvent.click(within(own).getByText("Мастер Пётр"));
  const signatures = within(own).getByRole("list", { name: "Подписи" });
  expect(signatures).toHaveTextContent("Сотрудник");
  expect(signatures).toHaveTextContent("Монтажник Анна подписал");
  expect(signatures).toHaveTextContent("PIN · версия 1");
  fireEvent.change(within(own).getByLabelText("PIN или пароль для подписи"), { target: { value: "000000" } });
  fireEvent.click(within(own).getByRole("button", { name: "Отозвать" }));
  expect(await within(own).findByRole("alert")).toHaveTextContent("Неверный PIN или пароль — подпись не принята.");
});

it("shows the HR journal to «Кадры»", async () => {
  serve({ ...OVERVIEW, hr: true, journal: [view({ status: "registered", status_label: "Зарегистрировано кадрами", waiting_for: null })] });
  render(<LeaveRequests />);
  const journal = await screen.findByRole("region", { name: "Журнал кадров" });
  expect(within(journal).getAllByText("Зарегистрировано кадрами").some((node) => node.classList.contains("kit-status"))).toBe(true);
});
