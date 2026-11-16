import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ProjectAddressee, Shipment } from "./api";
import { orderShipmentRows, ProjectShippingData, ShippingValue } from "./ShipmentGroupHeader";

afterEach(cleanup);

it("orders project groups by their newest shipment, then shipments newest first", () => {
  const dated = (id: string, shipped_at: string) => ({ id, shipped_at, deadline: null, created_at: "2026-11-01T12:00:00" } as Shipment);
  const rows = [{ project: "100К", shipment: dated("old", "2026-11-01") }, { project: "200К", shipment: dated("newest", "2026-11-15") },
    { project: "200К", shipment: dated("middle", "2026-11-10") }, { project: "001К", shipment: null }];
  expect(orderShipmentRows(rows).map((row) => row.shipment?.id ?? row.project)).toEqual(["newest", "middle", "old", "001К"]);
  expect(rows[0].shipment?.id).toBe("old");
});

describe("ProjectShippingData", () => {
  it.each([[null, "—"], ["ЭДО", "ЭДО"], ["БУМАГА", "Бумага"], ["БЕЗ УПД", "Без УПД"]])("ранний этап: %s без предупреждений о номере и файле", (upd_type, label) => {
    render(<ProjectShippingData data={{ upd_type } as ProjectAddressee} updRequired={false} />);
    const upd = screen.getByText(/^УПД:/);
    expect(upd).toHaveTextContent(`УПД: ${label}`);
    expect(upd.querySelector(".tone-warn")).toBeNull();
    expect(within(upd).queryByText(/№|файл/)).not.toBeInTheDocument();
  });

  it("по умолчанию требует тип, номер и файл УПД", () => {
    render(<ProjectShippingData />);
    const upd = within(screen.getByText(/^УПД:/));
    for (const value of ["не заполнено", "№ не заполнено", "без файла"]) expect(upd.getByText(value)).toHaveClass("tone-warn");
  });
});

describe("ShippingValue", () => {
  it("прочерк — «не заполнено», как пусто", () => {
    render(<ShippingValue label="Телефон" value=" - " />);
    expect(screen.getByText("не заполнено")).toBeInTheDocument();
  });

  it("«самовывоз» в получателе не показывается, в ТК — показывается", () => {
    const { container } = render(<><ShippingValue label="Получатель" value="самовывоз" /><ShippingValue label="ТК" value="Самовывоз" /></>);
    expect(container.textContent).toBe("ТК: Самовывоз");
  });

  it("значение показывается как есть", () => {
    render(<ShippingValue label="Получатель" value="Надир" />);
    expect(screen.getByText("Получатель: Надир")).toBeInTheDocument();
  });
});
