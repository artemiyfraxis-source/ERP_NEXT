// @vitest-environment node
// TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission: проверка из
// `WireBlankCatalogEditor.test.ts` (коммит 0a456d2), перенесена на `renderWireSvg` — в ERP нет редактора каталога заготовок.
import { describe, expect, it } from "vitest";
import { artworkJacketAnchor, artworkWireDiameter, renderWireSvg } from "./wire-blank-artwork";

describe("рисунок оконцовки провода (профиль C1)", () => {
  it("shares the C1 side profile and preserves jacket/seal/metal overlap", () => {
    const sealed = renderWireSvg({ left: "sealed", right: "sealed-pin", color: "#26609e" });
    expect(sealed).toContain('data-profile="three-rib-seal"');
    expect(sealed).toContain('data-profile="socket-contact"');
    expect(sealed).toContain('data-profile="pin-contact"');
    const jacket = sealed.indexOf('data-part="terminal-insulation"');
    const seal = sealed.indexOf('data-part="seal"');
    const metal = sealed.indexOf('data-part="terminal-shell"');
    expect(jacket).toBeLessThan(seal);
    expect(seal).toBeLessThan(metal);
    expect(sealed).toContain('data-view="side"');
  });

  it("keeps the jacket of the body as tall as the reference wire diameter", () => {
    const body = renderWireSvg({ left: "cut", right: "cut" });
    expect(body).toContain(`height="${artworkWireDiameter}"`);
    expect(artworkJacketAnchor).toBe(222);
  });
});
