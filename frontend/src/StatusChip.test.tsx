import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusChip } from "./StatusChip";

describe("StatusChip", () => {
  it("shows the status word in the tone of the vocabulary", () => {
    render(<StatusChip status="Недоукомплектован" />);
    const chip = screen.getByText("Недоукомплектован");
    expect(chip).toHaveClass("status-chip");
    expect(chip).toHaveAttribute("data-tone", "warning");
    expect(chip).not.toHaveAttribute("style");
  });

  it("shows the ERP word for a shop sheet word and keeps the original in the hint", () => {
    render(<StatusChip status="ОТГРУЖЕН" />);
    const chip = screen.getByText("Отгружено");
    expect(chip).toHaveAttribute("data-tone", "success");
    expect(chip).toHaveAttribute("title", "ОТГРУЖЕН");
  });

  it("takes a tone, a label and a hint from the screen", () => {
    render(<StatusChip status="СТОП" tone="info" title="клиент попросил подождать">стоит</StatusChip>);
    const chip = screen.getByText("стоит");
    expect(chip).toHaveAttribute("data-tone", "info");
    expect(chip).toHaveAttribute("title", "клиент попросил подождать");
  });

  it("is neutral for an unknown word and a dash for an empty one", () => {
    const { container } = render(<><StatusChip status="Непонятно" /><StatusChip status="  " /><StatusChip /></>);
    expect(screen.getByText("Непонятно")).toHaveAttribute("data-tone", "neutral");
    const empty = container.querySelectorAll(".status-chip.is-empty");
    expect(empty).toHaveLength(2);
    expect(empty[0].textContent).toBe("—");
  });

  it("counts repeats from two", () => {
    const { container } = render(<><StatusChip status="Новая" count={3} /><StatusChip status="Готово" count={1} /></>);
    expect(container.querySelectorAll(".status-chip-count")).toHaveLength(1);
    expect(container.querySelector(".status-chip-count")?.textContent).toBe("×3");
  });
});
