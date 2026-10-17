import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { PinoutBoard } from "./PinoutDiagram";
import { LinkSchemeSvg, schemeLayout, type LinkScheme } from "./PinoutLinks";
import { pinoutEnds, pinUnits } from "./pinoutCheck";
import { makeGen } from "./tcGenLogic";
import { D, harness, R } from "./tcGenRealHarness.fixture";

vi.mock("./api", () => ({ techcardsApi: { connectorFamilies: () => Promise.resolve({ match: null }) } }));

function board(onJoin = vi.fn()) {
  const S = harness(D), gen = makeGen(D, S);
  render(<PinoutBoard ends={pinoutEnds(gen, S)} links={gen.connections_().list} units={pinUnits(gen, S)} housings={{}} free={null}
    wireOptions={[]} warnings={[]} complex joins={{ splices: S.splices, tails: S.tails }} onJoin={onJoin}
    onAssign={vi.fn()} onUnassign={vi.fn()} onSwap={vi.fn()} onRecolor={vi.fn()} onAutoFill={() => ({ state: S, notes: [], warnings: [] })} />);
  return S;
}

it("«Сложная сборка»: no «Заполнить автоматически»; unknown housings get their contacts from the article", () => {
  board();
  expect(screen.queryByRole("button", { name: "Заполнить автоматически" })).toBeNull();
  // SMR-04V-B нет в базе разъёмов — четыре гнезда по артикулу, а не одно.
  const a4 = screen.getByRole("region", { name: "Схема разъёма А4" });
  expect(within(a4).getAllByRole("button").map((b) => b.textContent)).toEqual(["1", "2", "3", "4"]);
});

it("draws the links: a table per connector, splice dots С1–С4, four tails", () => {
  board();
  const svg = screen.getByRole("img", { name: "Провода с контакта на контакт, сростки и хвосты" });
  expect([...svg.querySelectorAll("[data-splice]")].map((n) => n.getAttribute("data-splice"))).toEqual(["С1", "С2", "С3", "С4"]);
  expect(svg.querySelectorAll('[data-free="tail"]')).toHaveLength(4);
  expect(svg.querySelectorAll(".pin-links-table")).toHaveLength(6);
  expect(screen.getByText(/17 пр\. · сростков 4 · хвостов 4/)).toBeInTheDocument();
});

it("a wire in hand: each end goes to a contact, a splice or a tail from the hand bar", () => {
  const onJoin = vi.fn();
  board(onJoin);
  // Провод 1 — красный 0,5: на стороне В он в сростке С1.
  fireEvent.click(screen.getAllByRole("button", { name: /^1НВ4 0,5 красный/ })[0]);
  const sideB = screen.getByRole("combobox", { name: "Конец В: 1" }) as HTMLSelectElement;
  expect(sideB.value).toBe("s1");
  fireEvent.change(screen.getByRole("combobox", { name: "Конец А: 1" }), { target: { value: "tail" } });
  expect(onJoin).toHaveBeenCalledWith({ idx: R.r5, unit: 0, side: "A" }, "tail");
});

/** Четыре провода А1·k → В1·k и свивка группами (ключи проводов "0".."3"). */
const fourWires = (twists: string[][]): LinkScheme => ({
  connectors: [
    { id: "a", key: "А1", side: "A", conn: "X", pins: 4, series: "", designation: "XS1", terminal: "" },
    { id: "b", key: "В1", side: "B", conn: "X", pins: 4, series: "", designation: "XS2", terminal: "" },
  ],
  wires: ["#d32f2f", "#ffffff", "#1976d2", "#212121"].map((color, k) => ({ key: String(k), num: String(k + 1), name: "UL1061 30AWG", color, stripe: null, length: 100,
    at: { a: k + 1, b: k + 1 }, a: [{ kind: "pin" as const, epId: "a", pin: k + 1 }], b: [{ kind: "pin" as const, epId: "b", pin: k + 1 }] })),
  splices: [], twists,
});

it.each([[[["0", "1"], ["2", "3"]]], [[["0", "2"], ["1", "3"]]]])("two twisted pairs are two separate twists, each with its own label (%j)", (twists) => {
  const scheme = fourWires(twists);
  const L = schemeLayout(scheme, 1600);
  expect(L.twists).toHaveLength(2);
  expect(Math.abs(L.twists[0].y - L.twists[1].y)).toBeGreaterThanOrEqual(20);
  const { container } = render(<LinkSchemeSvg scheme={scheme} layout={L} />);
  expect([...container.querySelectorAll(".pin-links-twist")].map((t) => t.textContent)).toEqual(["свитая пара", "свитая пара"]);
  expect(container.querySelectorAll('[data-twisted="true"]')).toHaveLength(4);
  // Спираль с пересечениями: участки «спереди» нарисованы поверх проводов группы.
  expect(container.querySelectorAll(".pin-links-front").length).toBe(4);
});

it("a wire outside any twist group stays a plain line; the scheme is read-only without onNet", () => {
  const scheme = fourWires([["0", "1"]]);
  const { container } = render(<LinkSchemeSvg scheme={scheme} layout={schemeLayout(scheme, 1600)} />);
  expect(container.querySelectorAll('[data-twisted="true"]')).toHaveLength(2);
  expect(container.querySelectorAll(".pin-links-line:not([data-twisted])")).toHaveLength(2);
  expect(container.querySelector("[data-net-cell]")).toBeNull();
});
