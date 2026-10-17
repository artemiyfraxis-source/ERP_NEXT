import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HelpLine, helpLineFor, helpOwner } from "./HelpLine";
import { InfoHint } from "./InfoHint";

function Panel() {
  return <div>
    <section aria-label="Панель">
      <button type="button">Объёмный свет</button><InfoHint>Затемнение краёв пайпов.</InfoHint>
      <div className="he-thickness-control"><label htmlFor="r">Радиус</label><output>24</output><InfoHint>Радиус изгибов.</InfoHint><input id="r" type="range" /></div>
      <button type="button">Без пояснения</button>
      <HelpLine idle="Наведите на настройку — здесь появится пояснение" />
    </section>
    <button type="button">Снаружи</button>
  </div>;
}
const line = () => document.querySelector(".help-line")!;

describe("HelpLine — одна строка пояснений вместо «i»", () => {
  it("shows the hint of the control under the pointer and clears it on a control without one", () => {
    render(<Panel />);
    expect(line().textContent).toBe("");
    expect(line().getAttribute("data-idle")).toBe("Наведите на настройку — здесь появится пояснение");
    fireEvent.pointerOver(screen.getByRole("button", { name: "Объёмный свет" }));
    expect(line().textContent).toBe("Затемнение краёв пайпов.");
    fireEvent.pointerOver(screen.getByRole("slider", { name: "Радиус" }));
    expect(line().textContent).toBe("Радиус изгибов.");
    fireEvent.pointerOver(screen.getByRole("button", { name: "Без пояснения" }));
    expect(line().textContent).toBe("");
  });

  it("follows keyboard focus and empties when the pointer leaves the panel", () => {
    render(<Panel />);
    fireEvent.focusIn(screen.getByRole("slider", { name: "Радиус" }));
    expect(line().textContent).toBe("Радиус изгибов.");
    fireEvent.pointerOut(screen.getByRole("slider", { name: "Радиус" }), { relatedTarget: screen.getByRole("button", { name: "Снаружи" }) });
    expect(line().textContent).toBe("");
  });

  it("describes the explained control for screen readers and opens no popup inside the panel", () => {
    render(<Panel />);
    const slider = screen.getByRole("slider", { name: "Радиус" });
    expect(slider).toHaveAccessibleDescription("Радиус изгибов.");
    const hint = document.querySelector<HTMLElement>(".he-thickness-control .info-hint")!;
    fireEvent.pointerEnter(hint);
    expect(hint.querySelector(".info-hint-popup")!.matches(":popover-open")).toBe(false);
  });

  it("picks the owner: labelled row, the control before the hint, else the parent; no line outside a panel", () => {
    const host = document.createElement("div");
    host.innerHTML = `<label>Тип<span class="a"></span></label><div><button>Свить</button><span class="b"></span></div><div><strong>Ветви</strong><span class="c"></span></div>`;
    expect(helpOwner(host.querySelector(".a")!)!.tagName).toBe("LABEL");
    expect(helpOwner(host.querySelector(".b")!)!.tagName).toBe("BUTTON");
    expect(helpOwner(host.querySelector(".c")!)!.tagName).toBe("DIV");
    expect(helpLineFor(host.querySelector(".c"))).toBeNull();
  });
});
