// @vitest-environment node
import { expect, it } from "vitest";
import { createEmptyHarnessDesign } from "./model";
import { volumeShadingEligible } from "./volume-shading";

it("allows volume lighting for one standalone physical pipe", () => {
  const document = createEmptyHarnessDesign();
  expect(volumeShadingEligible(document)).toBe(false);
  expect(volumeShadingEligible({
    ...document,
    physicalTopology: {
      snap: true,
      nodes: [
        { id: "from", position: { x: 0, y: 0 } },
        { id: "to", position: { x: 100, y: 0 } },
      ],
      segments: [{ id: "pipe", from: "from", to: "to", path: { kind: "routed", points: [] } }],
      routes: [],
    },
  })).toBe(true);
});
