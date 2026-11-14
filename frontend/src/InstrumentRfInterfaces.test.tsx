import { afterEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { InstrumentRfInterfaces } from "./InstrumentRfInterfaces";
import { type InstrumentRfDetails } from "./api";

const rf: InstrumentRfDetails = {
  version: 3, role: "vna", confirmed: false, source: "Паспорт", f_min_ghz: .0001, max_points: null, kit_definition: "",
  interfaces: [1, 2].map(() => ({ series: "N", body: "розетка", center: "гнездо", impedance_ohm: 50, f_max_ghz: 18, f_confirmed: false, f_source: "Паспорт" })),
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => vi.restoreAllMocks());

function setup(status = 200) {
  const puts: InstrumentRfDetails[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body)) as InstrumentRfDetails;
      puts.push(body);
      return status === 200 ? json({ rf: { ...body, version: 4 } }) : json({ detail: "Запись изменена — обновите" }, status);
    }
    return json({ rf });
  });
  const dirty = vi.fn();
  render(<InstrumentRfInterfaces instrumentId="vna" locked={false} onDirty={dirty} onBusy={vi.fn()} />);
  return { puts, dirty };
}

it("confirms exact interfaces using the loaded version and preserves independent RP contact fields", async () => {
  const { puts, dirty } = setup();
  expect(await screen.findByText(/Частота от: 0,0001 ГГц/)).toBeInTheDocument();
  fireEvent.click(await screen.findByText("Изменить интерфейсы"));
  fireEvent.change(screen.getByLabelText("Серия 1"), { target: { value: "RP-SMA" } });
  fireEvent.change(screen.getByLabelText("Корпус 1"), { target: { value: "вилка" } });
  expect(screen.getByLabelText("Центральный контакт 1")).toHaveValue("гнездо");
  fireEvent.click(screen.getByText("Подтвердить"));
  await screen.findByText("ВЧ-интерфейсы подтверждены");
  expect(puts).toHaveLength(1);
  expect(puts[0].version).toBe(3);
  expect(puts[0].confirmed).toBe(true);
  expect(puts[0].interfaces.every((p) => p.f_confirmed)).toBe(true);
  expect(puts[0].interfaces[0]).toMatchObject({ series: "RP-SMA", body: "вилка", center: "гнездо" });
  await waitFor(() => expect(dirty).toHaveBeenLastCalledWith(false));
});

it("saves an edited proposal without retaining confirmation and caps interfaces at two", async () => {
  const { puts, dirty } = setup();
  fireEvent.click(await screen.findByText("Изменить интерфейсы"));
  expect(screen.queryByText("Добавить интерфейс")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Источник данных"), { target: { value: "Другой паспорт" } });
  await waitFor(() => expect(dirty).toHaveBeenLastCalledWith(true));
  fireEvent.click(screen.getByText("Сохранить без подтверждения"));
  await screen.findByText("ВЧ-интерфейсы сохранены без подтверждения");
  expect(puts[0].confirmed).toBe(false);
  expect(puts[0].interfaces.every((p) => !p.f_confirmed)).toBe(true);
});

it("keeps unsaved values on conflict and requires a reload before another write", async () => {
  const { puts } = setup(409);
  fireEvent.click(await screen.findByText("Изменить интерфейсы"));
  fireEvent.change(screen.getByLabelText("Сопротивление 1, Ом"), { target: { value: "75" } });
  fireEvent.click(screen.getByText("Подтвердить"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Запись изменена");
  expect(screen.getByLabelText("Сопротивление 1, Ом")).toHaveValue(75);
  expect(screen.getByText("Подтвердить")).toBeDisabled();
  expect(puts).toHaveLength(1);
});

it("shows a fetch failure and retry instead of an empty confirmed state", async () => {
  vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Нет связи с ERP"));
  render(<InstrumentRfInterfaces instrumentId="vna" locked={false} onDirty={vi.fn()} onBusy={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с ERP");
  expect(screen.getByText("Обновить ВЧ-интерфейсы")).toBeEnabled();
  expect(screen.queryByText("Подтверждены")).not.toBeInTheDocument();
});
