// @vitest-environment node
import { Children, isValidElement, type ReactElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { E4WireSelectionMenu, type E4WireSelectionMenuProps } from "./E4WireSelectionMenu";

type TestElement = ReactElement<Record<string, unknown>>;

function descendants(node: ReactNode): readonly TestElement[] {
  const result: TestElement[] = [];
  Children.forEach(node, (child) => {
    if (!isValidElement<Record<string, unknown>>(child)) return;
    result.push(child);
    result.push(...descendants(child.props.children as ReactNode));
  });
  return result;
}

function textContent(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (!isValidElement<Record<string, unknown>>(node)) {
    let text = "";
    Children.forEach(node, (child) => { text += textContent(child); });
    return text;
  }
  return textContent(node.props.children as ReactNode);
}

function buttonWithText(tree: ReactNode, text: string): TestElement {
  const button = descendants(tree).find((element) =>
    element.type === "button" && textContent(element.props.children as ReactNode).includes(text));
  expect(button, `button containing ${text}`).toBeDefined();
  return button!;
}

function renderMenu(overrides: Partial<E4WireSelectionMenuProps> = {}) {
  const props: E4WireSelectionMenuProps = {
    selectedWireIds: ["w1", "w2"],
    crossingStyle: "none",
    differentialPair: null,
    screen: null,
    canClearGroup: false,
    onCrossingStyleChange: vi.fn(),
    onDifferentialPairChange: vi.fn(),
    onScreenChange: vi.fn(),
    onClearGroup: vi.fn(),
    ...overrides,
  };
  return { props, tree: E4WireSelectionMenu(props) };
}

describe("E4 wire selection menu handlers", () => {
  it("emits default differential-pair and screen settings and the selected crossing style", () => {
    const onCrossingStyleChange = vi.fn();
    const onDifferentialPairChange = vi.fn();
    const onScreenChange = vi.fn();
    const { tree } = renderMenu({
      onCrossingStyleChange,
      onDifferentialPairChange,
      onScreenChange,
    });

    (buttonWithText(tree, "Дуга").props.onClick as () => void)();
    (buttonWithText(tree, "Дифф. пара").props.onClick as () => void)();
    (buttonWithText(tree, "Экран").props.onClick as () => void)();

    expect(onCrossingStyleChange).toHaveBeenCalledWith("bridge");
    expect(onDifferentialPairChange).toHaveBeenCalledWith({ variant: 1, twistPitchMm: 25 });
    expect(onScreenChange).toHaveBeenCalledWith({ positionPercent: 50, terminalSide: "above" });
  });

  it("emits edits and removals for existing differential-pair and screen groups", () => {
    const onDifferentialPairChange = vi.fn();
    const onScreenChange = vi.fn();
    const onClearGroup = vi.fn();
    const { tree } = renderMenu({
      differentialPair: { variant: 1, twistPitchMm: 25 },
      screen: { positionPercent: 50 },
      canClearGroup: true,
      onDifferentialPairChange,
      onScreenChange,
      onClearGroup,
    });
    const elements = descendants(tree);
    const variantTwo = elements.find((element) =>
      element.type === "button" && textContent(element.props.children as ReactNode) === "2");
    const pitch = elements.find((element) => element.type === "input" && element.props.type === "number");
    const screenPosition = elements.find((element) => element.type === "input" && element.props.type === "range");
    expect(variantTwo).toBeDefined();
    expect(pitch).toBeDefined();
    expect(screenPosition).toBeDefined();

    (variantTwo!.props.onClick as () => void)();
    (pitch!.props.onChange as (event: { target: { value: string } }) => void)({ target: { value: "12.34" } });
    (screenPosition!.props.onChange as (event: { target: { value: string } }) => void)({ target: { value: "73" } });
    (buttonWithText(tree, "Дифф. пара").props.onClick as () => void)();
    (buttonWithText(tree, "Экран").props.onClick as () => void)();
    (buttonWithText(tree, "Разгруппировать").props.onClick as () => void)();

    expect(onDifferentialPairChange).toHaveBeenNthCalledWith(1, { variant: 2, twistPitchMm: 25 });
    expect(onDifferentialPairChange).toHaveBeenNthCalledWith(2, { variant: 1, twistPitchMm: 12.3 });
    expect(onDifferentialPairChange).toHaveBeenNthCalledWith(3, null);
    expect(onScreenChange).toHaveBeenNthCalledWith(1, { positionPercent: 73 });
    expect(onScreenChange).toHaveBeenNthCalledWith(2, null);
    expect(onClearGroup).toHaveBeenCalledOnce();
  });

  it("offers a temporary detached routing override", () => {
    const changes: boolean[] = [];
    const reroutes: number[] = [];
    const { tree } = renderMenu({
      onDetachedChange: value => changes.push(value),
      onReroute: () => reroutes.push(1),
    });
    const detach = buttonWithText(tree, "Открепить привязку");
    (detach.props.onClick as () => void)();
    expect(changes).toEqual([true]);
    (buttonWithText(tree, "Перестроить").props.onClick as () => void)();
    expect(reroutes).toEqual([1]);
  });

  it("switches the conducting terminal above, below and to both sides", () => {
    const onScreenChange = vi.fn();
    const { tree } = renderMenu({
      screen: { positionPercent: 42, terminalSide: "above" },
      onScreenChange,
    });

    (buttonWithText(tree, "Снизу").props.onClick as () => void)();
    (buttonWithText(tree, "С двух сторон").props.onClick as () => void)();

    expect(onScreenChange).toHaveBeenNthCalledWith(1, { positionPercent: 42, terminalSide: "below" });
    expect(onScreenChange).toHaveBeenNthCalledWith(2, { positionPercent: 42, terminalSide: "both" });
    expect(buttonWithText(tree, "Сверху").props["aria-pressed"]).toBe(true);
  });
});
