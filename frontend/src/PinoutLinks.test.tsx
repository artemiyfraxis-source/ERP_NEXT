import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { PinoutBoard } from "./PinoutDiagram";
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
