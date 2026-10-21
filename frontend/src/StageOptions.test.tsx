import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { resetStageOptions, StageOptionsPanel, useStageOption } from "./StageOptions";

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

const OPTION = { key: "procurement.today", block: "Закупки", title: "«Сегодня» закупщика", before: "Как было", after: "Как стало", enabled: false };

afterEach(() => { vi.restoreAllMocks(); resetStageOptions(); });

function Probe() {
  return <span>{useStageOption("procurement.today") ? "новое" : "было"}</span>;
}

it("is hidden in production, where the options are not available", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ available: false, options: [OPTION] }));
  const { container } = render(<StageOptionsPanel canSwitch />);
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

it("switches an option to «Новое» and every screen sees it", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) =>
    json({ available: true, options: [{ ...OPTION, enabled: init?.method === "PUT" }] }));
  render(<><StageOptionsPanel canSwitch /><Probe /></>);
  fireEvent.click(await screen.findByRole("button", { name: /Опции стенда/ }));
  expect(screen.getByText("было")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Новое" }));
  await waitFor(() => expect(screen.getByText("новое")).toBeInTheDocument());
  expect(screen.getByText("Как стало")).toBeInTheDocument();
});
