import { openPlace } from "./navigation";

/** Deep links of «Запросы КП» (CONTRACTS.md): `?section=procurement&tab=rfq&rfq=<id>[&letters=1|&invoices=1]` opens
 *  the request, `letters=1` its supplier letters, `invoices=1` its invoice-request letters. */

export type RfqPanel = "letters" | "invoices";

export type RfqLinkState = { rfq: string; letters: boolean; invoices: boolean };

/** The request and the panel the address bar asks for ("" — none). */
export function rfqLinkState(search = window.location.search): RfqLinkState {
  const params = new URLSearchParams(search);
  return { rfq: params.get("rfq") ?? "", letters: params.get("letters") === "1", invoices: params.get("invoices") === "1" };
}

/** The address of a request (for a link elsewhere in the ERP): relative, the shell resolves it. */
export function rfqHref(rfqId: string, panel?: RfqPanel): string {
  const params = new URLSearchParams({ section: "procurement", tab: "rfq", rfq: rfqId });
  if (panel) params.set(panel, "1");
  return `?${params.toString()}`;
}

/** Open a request from anywhere in the app without a reload: the shell remounts «Лист закупки» on the URL. */
export function openRfq(rfqId: string, panel?: RfqPanel) {
  openPlace("procurement", { tab: "rfq", rfq: rfqId, ...(panel ? { [panel]: "1" } : {}) });
}

/** Keep the address bar on the request shown (and its open panels) without a new history entry. */
export function syncRfqUrl(rfqId: string, panels: { letters?: boolean; invoices?: boolean } = {}) {
  const url = new URL(window.location.href);
  if (url.searchParams.get("section") !== "procurement") return;
  url.searchParams.set("tab", "rfq");
  if (rfqId) url.searchParams.set("rfq", rfqId); else url.searchParams.delete("rfq");
  for (const panel of ["letters", "invoices"] as const) {
    if (panels[panel]) url.searchParams.set(panel, "1"); else url.searchParams.delete(panel);
  }
  if (url.href !== window.location.href) window.history.replaceState(window.history.state, "", url);
}
