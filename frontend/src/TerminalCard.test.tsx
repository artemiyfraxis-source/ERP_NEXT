import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReferencesPage } from "./ReferencesPage";
import { distributorLink, profileLines, rangeText, terminalCardLines, type TerminalCardData } from "./TerminalCard";
import type { Principal } from "./api";

const admin: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000001", email: "admin@example.test", name: "Администратор", department: "ERP" },
  roles: ["admin"], access_level: "admin", permissions: { erpref: "edit" },
};
const viewer: Principal = { ...admin, roles: ["foreman"], access_level: "foreman", permissions: { erpref: "view" } };

const EMPTY: TerminalCardData = {
  article: "", article_bag: "", gender: "", type: "", maker: "", series: "", product: "", step: "", strip: "", l_plus: "",
  l_minus: "", applicator: "", crimp_height: "", ins_crimp_height: "", pull_force_min: "", pull_force_max: "", awg_from: "",
  awg_to: "", mm2_from: "", mm2_to: "", ins_od_from: "", ins_od_to: "", material: "", max_current: "", datasheet: "",
};
const MINI_FIT: TerminalCardData = {
  ...EMPTY, article: "39000038", article_bag: "39000039", gender: "Female", maker: "Molex", series: "Mini-Fit Jr.",
  product: "5556", step: "4,2", strip: "3,0", applicator: "M5", crimp_height: "24 AWG: 2,21\n22 AWG: 2,24",
  pull_force_min: "24 AWG: 98", awg_from: "24", awg_to: "18", ins_od_from: "1,3", ins_od_to: "3,1",
  material: "Brass / Tin", max_current: "9", datasheet: "https://www.molex.com/en-us/products/part-detail/39000038",
};
const record = {
  id: "30000000-0000-0000-0000-000000000001", tab: "ter", display_text: "Molex | Mini-Fit Jr. | 5556", source_sheet: "",
  fields: { "Артикул контакта (REEL)": "39000038", "Тип контакта": "Female" }, card: MINI_FIT,
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

function mockApi() {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path.startsWith("/api/references/tech-operations/crimp-codes")) return json({ owners: {}, missing: [] });
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, records: [record] });
    if (path === "/api/references/bases") return json({ bases: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
}

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); window.history.replaceState({}, "", "/"); });

it("keeps the side of a range the spec gives and never fills the other", () => {
  expect(rangeText("0,8", "1,5")).toBe("0,8–1,5");
  expect(rangeText("", "1,7")).toBe("до 1,7");
  expect(rangeText("0,8", "")).toBe("от 0,8");
  expect(rangeText("2", "2")).toBe("2");
  expect(rangeText("", "")).toBe("");
});

it("shows mirror quirks as they are: «-» is empty, a distributor's datasheet is marked", () => {
  const shown = Object.fromEntries(terminalCardLines({ ...EMPTY, material: "-", max_current: "—" }).contact);
  expect([shown["Материал / покрытие"], shown["Макс. ток, А"]]).toEqual(["", ""]);
  expect(distributorLink("https://www.digikey.com/en/products/detail/x")).toBe(true);
  expect(distributorLink("https://www.electro-mpo.ru/card/1")).toBe(true);
  expect(distributorLink("https://www.molex.com/x")).toBe(false);
  expect(distributorLink("https://kvt.su/prod/x")).toBe(false);
  expect(distributorLink("не ссылка")).toBe(false);
});

it("puts one AWG per line in a crimp profile", () => {
  expect(profileLines("24 AWG: 0,78–0,88; 22 AWG: 0,80–0,90; 18-16 AWG: 1,00")).toBe("24 AWG: 0,78–0,88\n22 AWG: 0,80–0,90\n18-16 AWG: 1,00");
  expect(profileLines("1,38")).toBe("1,38");
});

it("reads the card in Russian: gender as штырь / гнездо, wire by AWG and mm², empty stays empty", () => {
  const { contact, crimp } = terminalCardLines({ ...MINI_FIT, mm2_from: "0,2", mm2_to: "0,8", l_plus: "0,5" });
  const shown = Object.fromEntries([...contact, ...crimp].map(([title, value]) => [title, value]));
  expect(shown["Пол"]).toBe("Гнездо (мама)");
  expect(shown["Провод"]).toBe("24–18 AWG · 0,2–0,8 мм²");
  expect(shown["Диаметр изоляции, мм"]).toBe("1,3–3,1");
  expect(shown["Высота обжима изоляции, мм"]).toBe(""); // null-spec: nothing averaged or guessed
  expect(shown["L+ / L−, мм"]).toBe("L+ 0,5");
  expect(terminalCardLines({ ...EMPTY, gender: "Male" }).contact[0][1]).toBe("Штырь (папа)");
  expect(terminalCardLines({ ...EMPTY, gender: "Обжимной контакт" }).contact[0][1]).toBe("");
});

it("opens the contact card from a «Терминалы» row, keeps it in the address and closes it", async () => {
  mockApi();
  render(<ReferencesPage principal={admin} initialTab="ter" />);
  fireEvent.click(await screen.findByText("39000038")); // an article of digits is a code: not «39 000 038»

  const card = await screen.findByRole("complementary", { name: "Карточка контакта" });
  expect(within(card).getByText("Molex · Mini-Fit Jr. · 5556")).toBeInTheDocument();
  for (const [title, value] of [["Пол", "Гнездо (мама)"], ["Артикул BAG (россыпь)", "39000039"], ["Материал / покрытие", "Brass / Tin"],
    ["Макс. ток, А", "9"], ["Диаметр изоляции, мм", "1,3–3,1"], ["Провод", "24–18 AWG"], ["Аппликатор", "M5"]]) {
    expect(within(card).getByText(title).nextElementSibling).toHaveTextContent(value);
  }
  expect(within(card).getByText("Высота обжима изоляции, мм").nextElementSibling).toHaveTextContent("—");
  expect(within(card).getByRole("link", { name: "molex.com ↗" })).toHaveAttribute("href", MINI_FIT.datasheet);
  expect(window.location.search).toBe(`?terminal=${record.id}`);

  fireEvent.click(within(card).getByRole("button", { name: "Править запись" }));
  expect(await screen.findByRole("button", { name: "Сохранить запись" })).toBeInTheDocument();

  fireEvent.keyDown(window, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("complementary", { name: "Карточка контакта" })).toBeNull());
  expect(window.location.search).toBe("");
});

it("shows the card to a viewer without editing", async () => {
  mockApi();
  window.history.replaceState({}, "", `/?terminal=${record.id}`);
  render(<ReferencesPage principal={viewer} initialTab="ter" />);
  const card = await screen.findByRole("complementary", { name: "Карточка контакта" });
  expect(within(card).getByText("39000038", { selector: "b" })).toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "Править запись" })).toBeNull();
});
