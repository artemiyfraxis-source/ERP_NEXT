import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Readiness } from "./ShipmentData";

afterEach(cleanup);
const source = { receiver: "Получатель", phone: "+7 900", shipping_point: "Москва", carrier: "СДЭК", terms: "ДОСТАВКА ТК", upd_type: "ЭДО", upd_number: "123",
  missing_data: [], missing_ship: ["трек-номер"], ready_for_track: true, ready_to_ship: false, track_number: "" };

describe("Readiness", () => {
  it("manager data ready still asks the warehouse for a track instead of promising dispatch readiness", () => {
    render(<Readiness source={source} gaps={[]} />);
    expect(screen.queryByText("Готово к отгрузке")).not.toBeInTheDocument();
    expect(screen.getByText("Ждёт трек от склада")).toBeInTheDocument();
  });
  it("retains real manager blockers and accepts full readiness only after the server reply", () => {
    const view = render(<Readiness source={source} gaps={["телефон получателя"]} />);
    expect(screen.getByText("Ждёт данных менеджера")).toHaveAttribute("title", "Не хватает: телефон получателя");
    view.rerender(<Readiness source={{ ...source, ready_to_ship: true, missing_ship: [], track_number: "TR-1" }} gaps={[]} />);
    expect(screen.getByText("Готово к отгрузке")).toBeInTheDocument();
  });
  it("does not claim dispatch ready when the server gate is false and there is another warehouse blocker", () => {
    render(<Readiness source={{ ...source, missing_ship: ["разрешение на отгрузку без УПД"] }} gaps={[]} />);
    expect(screen.queryByText("Готово к отгрузке")).not.toBeInTheDocument();
  });
  it("track-only gaps stay a warehouse step when the shared strip includes the track chip", () => {
    render(<Readiness source={source} gaps={["трек-номер"]} withTrack />);
    expect(screen.queryByText("Ждёт данных менеджера")).not.toBeInTheDocument();
    expect(screen.getByText("Ждёт трек от склада")).toBeInTheDocument();
  });
  it("complete project data without server dispatch readiness only confirms that the data is entered", () => {
    const { ready_to_ship: _ready, missing_ship: _missing, ...project } = source;
    render(<Readiness source={project} gaps={[]} />);
    expect(screen.queryByText("Готово к отгрузке")).not.toBeInTheDocument();
    expect(screen.getByText("Данные внесены")).toBeInTheDocument();
  });
  it("explicit track-only gaps work even without the optional shipment fields", () => {
    const { ready_to_ship: _ready, missing_ship: _missing, ...project } = source;
    render(<Readiness source={project} gaps={["трек-номер"]} />);
    expect(screen.getByText("Ждёт трек от склада")).toBeInTheDocument();
  });
});
