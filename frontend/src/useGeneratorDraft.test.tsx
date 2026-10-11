import { act, fireEvent, render, screen } from "@testing-library/react";
import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, UnknownResultError, type GeneratorDraftRecord } from "./api";
import { buildMark, draftKey, type DraftServer, GeneratorDraftNote, OFFLINE_TEXT, useGeneratorDraft } from "./useGeneratorDraft";

/** Сервер-стенд: черновики в памяти, версия +1 на запись, 409 на устаревшую версию; down — сервер недоступен. */
function fakeServer() {
  const drafts = new Map<string, GeneratorDraftRecord>();
  const state = { down: false, loseAnswer: false };
  const at = (project: string, index: string) => `${project}|${index}`;
  const offline = () => { if (state.down) throw new TypeError("Failed to fetch"); };
  const server = {
    drafts, state,
    get: vi.fn(async (project: string, index: string) => { offline(); return { draft: drafts.get(at(project, index)) ?? null }; }),
    save: vi.fn(async (project: string, index: string, body: { payload: unknown; build_fingerprint: string; base_version: number }) => {
      offline();
      const current = drafts.get(at(project, index));
      if (current && current.version !== body.base_version) {
        throw new ApiError("Черновик изменён в другом месте", 409, {
          message: "Черновик изменён в другом месте", code: "generator_draft_conflict", version: current.version, updated_at: current.updated_at,
        });
      }
      const now = new Date().toISOString();
      const record: GeneratorDraftRecord = {
        project, item_index: index, payload: JSON.parse(JSON.stringify(body.payload)), build_fingerprint: body.build_fingerprint,
        version: (current?.version ?? 0) + 1, created_at: current?.created_at ?? now, updated_at: now,
      };
      drafts.set(at(project, index), record);
      if (state.loseAnswer) { state.loseAnswer = false; throw new UnknownResultError(); }
      return record;
    }),
    remove: vi.fn(async (project: string, index: string) => { offline(); return { deleted: drafts.delete(at(project, index)) }; }),
    /** Черновик, сохранённый другим устройством. */
    put(text: string, version: number, updatedAt: string, built = buildMark(null)) {
      drafts.set(at("P-1", "КБ1"), {
        project: "P-1", item_index: "КБ1", payload: { text }, build_fingerprint: built, version, created_at: updatedAt, updated_at: updatedAt,
      });
    },
    text: () => (drafts.get(at("P-1", "КБ1"))?.payload as { text?: string } | undefined)?.text,
  };
  return server;
}

let server = fakeServer();

type Props = { user?: string; index?: string; document?: Record<string, unknown> | null; seed?: string; auto?: string; api?: DraftServer | null };

/** Форма-стенд: засев при открытии КБ, как в генераторе (layout-эффект), поле ввода и «Создано» — успешная сборка. */
function Form({ user = "u1", index = "КБ1", document = null, seed = "", auto, api }: Props) {
  const [text, setText] = useState("");
  const value = useMemo(() => ({ text }), [text]);
  const draft = useGeneratorDraft({ user, project: "P-1", index, document, value, apply: (next) => setText(next.text),
    server: api === undefined ? server : api });
  useLayoutEffect(() => { setText(draft.open({ text: seed }).text); }, [index]); // eslint-disable-line react-hooks/exhaustive-deps
  // Автоподбор после засева (как stickTemplates) — не правка пользователя.
  useEffect(() => { if (auto) setText((current) => current + auto); }, [auto, index]);
  return <div {...draft.bind}>
    <input aria-label="Поле" value={text} onChange={(event) => setText(event.target.value)} />
    <GeneratorDraftNote draft={draft} />
    <button onClick={draft.clear}>Создано</button>
  </div>;
}

const KEY = draftKey("u1", "P-1", "КБ1");
const stored = (key = KEY) => JSON.parse(window.localStorage.getItem(key) ?? "null");
const type = (text: string) => fireEvent.change(screen.getByLabelText("Поле"), { target: { value: text } });

const settle = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

beforeEach(() => { vi.useFakeTimers(); window.localStorage.clear(); server = fakeServer(); });
afterEach(() => { vi.useRealTimers(); window.localStorage.clear(); });

it("saves a second after the last change and shows the time", () => {
  render(<Form />);
  type("А1 XHP");
  expect(screen.getByText("Сохраняю черновик…")).toBeInTheDocument();
  expect(stored()).toBeNull();
  act(() => { vi.advanceTimersByTime(1000); });
  expect(stored()).toMatchObject({ rev: 1, value: { text: "А1 XHP" } });
  expect(screen.getByText(/^Черновик сохранён \d\d:\d\d$/)).toBeInTheDocument();
});

it("keeps what was typed through a tab switch (unmount before the debounce) and restores it with «Отбросить»", () => {
  const first = render(<Form seed="засев" />);
  type("введено");
  first.unmount();
  expect(stored()).toMatchObject({ value: { text: "введено" } });

  render(<Form seed="засев" />);
  expect(screen.getByLabelText("Поле")).toHaveValue("введено");
  expect(screen.getByText(/Восстановлен несохранённый черновик от \d\d\.\d\d\.\d{4}, \d\d:\d\d/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Отбросить" }));
  expect(screen.getByLabelText("Поле")).toHaveValue("засев");
  expect(stored()).toBeNull();
  expect(screen.queryByText(/Восстановлен/)).not.toBeInTheDocument();
  act(() => { vi.advanceTimersByTime(2000); });
  expect(stored()).toBeNull();
});

it("clears the draft after a successful build", () => {
  const first = render(<Form />);
  type("собрано");
  act(() => { vi.advanceTimersByTime(1000); });
  fireEvent.click(screen.getByRole("button", { name: "Создано" }));
  expect(stored()).toBeNull();
  first.unmount();
  expect(stored()).toBeNull();
  render(<Form />);
  expect(screen.getByLabelText("Поле")).toHaveValue("");
  expect(screen.queryByText(/Восстановлен/)).not.toBeInTheDocument();
});

it("does not save the seeding or automatic picks before the user touches the form", () => {
  const first = render(<Form seed="засев" auto="+шаблон" />);
  expect(screen.getByLabelText("Поле")).toHaveValue("засев+шаблон");
  act(() => { vi.advanceTimersByTime(2000); });
  first.unmount();
  expect(stored()).toBeNull();
});

it("drops a draft made before the card was rebuilt", () => {
  const before = { build_config: { ops: ["cutWire"] } };
  const after = { build_config: { ops: ["cutWire", "prsTerm"] } };
  window.localStorage.setItem(KEY, JSON.stringify({ v: 1, rev: 3, savedAt: "2026-10-10T18:00:00Z", built: buildMark(before), value: { text: "старое" } }));
  render(<Form document={after} seed="засев" />);
  expect(screen.getByLabelText("Поле")).toHaveValue("засев");
  expect(stored()).toBeNull();
});

it("restores a draft made over the current build", () => {
  const document = { build_config: { ops: ["cutWire"] } };
  window.localStorage.setItem(KEY, JSON.stringify({ v: 1, rev: 3, savedAt: "2026-10-10T18:58:00Z", built: buildMark(document), value: { text: "черновик" } }));
  render(<Form document={document} />);
  expect(screen.getByLabelText("Поле")).toHaveValue("черновик");
  type("черновик 2");
  act(() => { vi.advanceTimersByTime(1000); });
  expect(stored()).toMatchObject({ rev: 4, value: { text: "черновик 2" } });
});

it("never overwrites a newer draft written by another tab and says so", () => {
  render(<Form />);
  type("моё");
  act(() => { vi.advanceTimersByTime(1000); });
  // Другая вкладка открыла тот же черновик и записала новее.
  window.localStorage.setItem(KEY, JSON.stringify({ ...stored(), rev: 5, value: { text: "из другой вкладки" } }));
  type("моё 2");
  act(() => { vi.advanceTimersByTime(1000); });
  expect(stored()).toMatchObject({ rev: 5, value: { text: "из другой вкладки" } });
  expect(screen.getByText(/Черновик изменён в другой вкладке в \d\d:\d\d — оставлен он/)).toBeInTheDocument();
  type("моё 3");
  act(() => { vi.advanceTimersByTime(1000); });
  expect(stored()).toMatchObject({ rev: 5 });
});

it("keeps drafts apart per КБ and per user, and saves the previous КБ on switching", () => {
  const view = render(<Form />);
  type("КБ1 данные");
  view.rerender(<Form index="КБ2" />);
  expect(stored()).toMatchObject({ value: { text: "КБ1 данные" } });
  expect(screen.getByLabelText("Поле")).toHaveValue("");
  view.rerender(<Form index="КБ1" />);
  expect(screen.getByLabelText("Поле")).toHaveValue("КБ1 данные");
  view.unmount();
  render(<Form user="u2" api={fakeServer()} />); // на сервере у другого человека свои черновики
  expect(screen.getByLabelText("Поле")).toHaveValue("");
});

it("saves at once when the browser tab is hidden", () => {
  render(<Form />);
  type("ухожу");
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  expect(stored()).toMatchObject({ value: { text: "ухожу" } });
});

it("keeps no draft without a user", () => {
  const view = render(<Form user="" />);
  type("аноним");
  view.unmount();
  expect(window.localStorage.length).toBe(0);
});

it("reports a storage failure instead of failing silently", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("QuotaExceededError"); });
  render(<Form />);
  type("много");
  act(() => { vi.advanceTimersByTime(1000); });
  expect(screen.getByText("Черновик не сохранён: хранилище браузера недоступно.")).toBeInTheDocument();
  vi.restoreAllMocks();
});

/* ── Сервер (владелец 11.10): черновик доходит до другого компьютера и браузера ── */

const field = () => screen.getByLabelText("Поле");
const local = (record: Record<string, unknown>) =>
  window.localStorage.setItem(KEY, JSON.stringify({ v: 1, built: buildMark(null), ...record }));
const saveBody = (text: string, base: number) =>
  ["P-1", "КБ1", { payload: { text }, build_fingerprint: buildMark(null), base_version: base }, false];

it("sends the draft to the server two seconds after the local save, each save over the last version", async () => {
  vi.setSystemTime(new Date("2026-10-11T09:00:00Z"));
  render(<Form />);
  await settle();
  type("А1");
  await settle(1000);
  expect(stored()).toMatchObject({ value: { text: "А1" }, server: 0, synced: false });
  expect(server.save).not.toHaveBeenCalled();
  await settle(2000);
  expect(server.save).toHaveBeenCalledWith(...saveBody("А1", 0));
  expect(stored()).toMatchObject({ server: 1, synced: true });
  type("А1 XHP");
  await settle(3000);
  expect(server.save).toHaveBeenLastCalledWith(...saveBody("А1 XHP", 1));
  expect(server.drafts.get("P-1|КБ1")).toMatchObject({ version: 2, payload: { text: "А1 XHP" } });
  expect(screen.getByText(/^Черновик сохранён \d\d:\d\d$/)).toBeInTheDocument();
});

it("sends at once on leaving the form (tab switch before the debounce)", async () => {
  const view = render(<Form />);
  await settle();
  type("ухожу");
  view.unmount();
  await settle();
  expect(server.text()).toBe("ухожу");
});

it("opens the newer draft typed on another computer", async () => {
  vi.setSystemTime(new Date("2026-10-11T09:00:00Z"));
  server.put("с другого компьютера", 2, "2026-10-11T08:30:00Z");
  render(<Form seed="засев" />);
  expect(field()).toHaveValue("засев");
  await settle();
  expect(field()).toHaveValue("с другого компьютера");
  expect(screen.getByText(/Восстановлен несохранённый черновик от/)).toBeInTheDocument();
  expect(stored()).toMatchObject({ server: 2, synced: true, value: { text: "с другого компьютера" } });
  expect(server.save).not.toHaveBeenCalled();
  type("дальше");
  await settle(3000);
  expect(server.drafts.get("P-1|КБ1")).toMatchObject({ version: 3, payload: { text: "дальше" } });
});

it("lets a copy already sent from here give way to a newer server version without a word", async () => {
  server.put("новее", 2, "2026-10-11T08:30:00Z");
  local({ rev: 4, savedAt: "2026-10-11T08:40:00Z", value: { text: "отправленное" }, server: 1, synced: true });
  render(<Form />);
  expect(field()).toHaveValue("отправленное");
  await settle();
  expect(field()).toHaveValue("новее");
  expect(screen.queryByText(/другом устройстве/)).not.toBeInTheDocument();
  expect(stored()).toMatchObject({ rev: 5, server: 2, synced: true });
});

it("keeps this computer's newer draft and sends it over the server version", async () => {
  vi.setSystemTime(new Date("2026-10-11T09:00:00Z"));
  server.put("с другого компьютера", 2, "2026-10-11T08:00:00Z");
  local({ rev: 3, savedAt: "2026-10-11T08:40:00Z", value: { text: "здесь" }, server: 1, synced: false });
  render(<Form />);
  await settle();
  expect(field()).toHaveValue("здесь");
  expect(screen.getByText(/^Черновик на другом устройстве \(\d\d:\d\d\) старее этого — оставлен этот\.$/)).toBeInTheDocument();
  expect(server.save).toHaveBeenCalledWith(...saveBody("здесь", 2));
  expect(server.drafts.get("P-1|КБ1")).toMatchObject({ version: 3, payload: { text: "здесь" } });
  expect(stored()).toMatchObject({ server: 3, synced: true });
});

it("opens the server draft when it is newer than unsent edits here, and says so", async () => {
  vi.setSystemTime(new Date("2026-10-11T09:00:00Z"));
  server.put("с другого компьютера", 2, "2026-10-11T08:30:00Z");
  local({ rev: 3, savedAt: "2026-10-11T08:00:00Z", value: { text: "старое здесь" }, server: 1, synced: false });
  render(<Form />);
  await settle();
  expect(field()).toHaveValue("с другого компьютера");
  expect(screen.getByText(/^Черновик изменён на другом устройстве в \d\d:\d\d — открыт он; правки этого компьютера от \d\d:\d\d старее\.$/)).toBeInTheDocument();
  expect(server.save).not.toHaveBeenCalled();
});

it("a draft typed offline here goes to the server at the next open", async () => {
  local({ rev: 2, savedAt: "2026-10-11T08:00:00Z", value: { text: "без сети" }, server: 0, synced: false });
  render(<Form />);
  await settle();
  expect(server.save).toHaveBeenCalledWith(...saveBody("без сети", 0));
  expect(stored()).toMatchObject({ server: 1, synced: true });
});

it("stops sending when another device saved over the same version, keeps typing here, and says so", async () => {
  render(<Form />);
  await settle();
  type("моё");
  await settle(3000);
  expect(server.text()).toBe("моё");
  server.put("с другого устройства", 2, new Date().toISOString());
  type("моё 2");
  await settle(3000);
  expect(screen.getByText(/^Черновик изменён на другом устройстве в \d\d:\d\d — на сервере оставлен он, правки здесь сохраняются только на этом компьютере\./)).toBeInTheDocument();
  expect(server.text()).toBe("с другого устройства");
  expect(stored()).toMatchObject({ value: { text: "моё 2" }, synced: false });
  const sent = server.save.mock.calls.length;
  type("моё 3");
  await settle(3000);
  expect(server.save.mock.calls.length).toBe(sent);
  expect(stored()).toMatchObject({ value: { text: "моё 3" } });
  expect(server.text()).toBe("с другого устройства");
});

it("keeps the draft here when the server is down and sends it once it is back", async () => {
  server.state.down = true;
  render(<Form />);
  await settle();
  type("без сети");
  await settle(1000);
  expect(stored()).toMatchObject({ value: { text: "без сети" }, synced: false });
  await settle(2000);
  expect(screen.getByText(OFFLINE_TEXT)).toBeInTheDocument();
  type("без сети 2");
  await settle(1000);
  expect(screen.getByText(OFFLINE_TEXT)).toBeInTheDocument();
  server.state.down = false;
  type("снова в сети");
  await settle(3000);
  expect(server.text()).toBe("снова в сети");
  expect(screen.getByText(/^Черновик сохранён \d\d:\d\d$/)).toBeInTheDocument();
  expect(stored()).toMatchObject({ synced: true });
});

it("takes its own save whose answer was lost for its own, not for another device's", async () => {
  render(<Form />);
  await settle();
  server.state.loseAnswer = true;
  type("а");
  await settle(3000);
  expect(server.text()).toBe("а");
  expect(screen.getByText(OFFLINE_TEXT)).toBeInTheDocument();
  type("аб");
  await settle(5000);
  expect(server.text()).toBe("аб");
  expect(screen.queryByText(/другом устройстве/)).not.toBeInTheDocument();
  expect(stored()).toMatchObject({ synced: true, server: 2 });
});

it("«Создано» and «Отбросить» remove the draft on the server too", async () => {
  const built = render(<Form />);
  await settle();
  type("собрано");
  await settle(3000);
  expect(server.text()).toBe("собрано");
  fireEvent.click(screen.getByRole("button", { name: "Создано" }));
  await settle();
  expect(server.drafts.size).toBe(0);
  expect(stored()).toBeNull();
  await settle(5000);
  expect(server.drafts.size).toBe(0);
  built.unmount();

  server.put("с другого компьютера", 1, "2026-10-11T08:30:00Z");
  render(<Form seed="засев" />);
  await settle();
  expect(field()).toHaveValue("с другого компьютера");
  fireEvent.click(screen.getByRole("button", { name: "Отбросить" }));
  expect(field()).toHaveValue("засев");
  await settle(5000);
  expect(server.drafts.size).toBe(0);
  expect(stored()).toBeNull();
});

it("drops the copy here when the draft was built or discarded on another device", async () => {
  local({ rev: 2, savedAt: "2026-10-11T08:00:00Z", value: { text: "отправленное" }, server: 1, synced: true });
  render(<Form seed="засев" />);
  expect(field()).toHaveValue("отправленное");
  await settle();
  expect(field()).toHaveValue("засев");
  expect(stored()).toBeNull();
  expect(screen.queryByText(/Восстановлен/)).not.toBeInTheDocument();
});

it("ignores a server draft made over another build of the card, and saves over its version", async () => {
  server.put("старая сборка", 3, "2026-10-11T08:30:00Z", "other-build");
  render(<Form seed="засев" />);
  await settle();
  expect(field()).toHaveValue("засев");
  type("новое");
  await settle(3000);
  expect(server.drafts.get("P-1|КБ1")).toMatchObject({ version: 4, payload: { text: "новое" } });
});

it("keeps the draft only in the browser without a server layer", async () => {
  render(<Form api={null} />);
  type("только здесь");
  await settle(5000);
  expect(stored()).toMatchObject({ value: { text: "только здесь" } });
  expect(server.save).not.toHaveBeenCalled();
});
