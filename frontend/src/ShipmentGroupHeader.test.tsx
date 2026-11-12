import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ShippingValue } from "./ShipmentGroupHeader";

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
