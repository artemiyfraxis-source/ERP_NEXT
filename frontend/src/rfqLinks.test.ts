import { afterEach, expect, it, vi } from "vitest";
import { openRfq, rfqHref, rfqLinkState, syncRfqUrl } from "./rfqLinks";

afterEach(() => window.history.replaceState({}, "", "/"));

it("reads the request and its panel from the address", () => {
  expect(rfqLinkState("?section=procurement&tab=rfq&rfq=r1&letters=1")).toEqual({ rfq: "r1", letters: true, invoices: false });
  expect(rfqLinkState("?section=procurement&tab=rfq&rfq=r1&invoices=1")).toEqual({ rfq: "r1", letters: false, invoices: true });
  expect(rfqLinkState("?section=procurement")).toEqual({ rfq: "", letters: false, invoices: false });
});

it("builds and opens the link without a reload, other parameters dropped", () => {
  expect(rfqHref("r1", "letters")).toBe("?section=procurement&tab=rfq&rfq=r1&letters=1");
  window.history.replaceState({}, "", "/?section=procurement&tab=today&q=x");
  const popped = vi.fn();
  window.addEventListener("popstate", popped);
  openRfq("r7", "invoices");
  window.removeEventListener("popstate", popped);
  expect(window.location.search).toBe("?section=procurement&tab=rfq&rfq=r7&invoices=1");
  expect(popped).toHaveBeenCalledTimes(1);
});

it("keeps the address on the shown request and panels, only inside «Лист закупки»", () => {
  window.history.replaceState({}, "", "/?section=procurement&tab=rfq&rfq=r1&letters=1");
  syncRfqUrl("r2", { invoices: true });
  expect(window.location.search).toBe("?section=procurement&tab=rfq&rfq=r2&invoices=1");
  window.history.replaceState({}, "", "/?section=requests");
  syncRfqUrl("r3");
  expect(window.location.search).toBe("?section=requests");
});
