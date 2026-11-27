import { afterEach, describe, expect, it, vi } from "vitest";
import type { DispatchShipment } from "./productionIntakeApi";
import { documentsBody, editableKeys, headPatch, PartialSaveError, saveShipment } from "./shipmentSaves";

const shipment = (over: Partial<DispatchShipment> = {}) => ({
  id: "s1", number: "ОТГ-1", version: 3, created_at: "2026-11-20", status: "Упакована", customer: "Заказчик", receiver: "Организация",
  shipping_point: "Москва", contact: "Иванов", phone: "+7 900", terms: "ДОСТАВКА ТК", carrier: "СДЭК", deadline: null,
  upd_type: "ЭДО", upd_number: "У-1", upd_date: "2026-11-20", upd_url: "/api/shipping/files/f1", pl_number: "PL1", dn_number: "DN1",
  track_number: "TRK-1", packed_at: "2026-11-20", shipped_at: null, manager: "", note: "", tk_note: "", places: 1, weight: "", boxes: [],
  lines: [], inherited: [], missing_data: [], missing_ship: [], ...over,
}) as DispatchShipment;

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let calls: Array<{ url: string; method: string; body: Record<string, unknown> }> = [];
function stub(answer: (url: string, body: Record<string, unknown>) => Response) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? "{}"));
    calls.push({ url: String(input), method: init?.method ?? "GET", body });
    return answer(String(input), body);
  }));
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("shipmentSaves", () => {
  it("head patch keeps only changed fields; carrier brings its terms, pickup sets the pickup carrier", () => {
    expect(headPatch(shipment(), { phone: " +7 900 ", contact: "Петров" })).toEqual({ contact: "Петров" });
    expect(headPatch(shipment({ terms: "САМОВЫВОЗ", carrier: "Самовывоз" }), { carrier: "СДЭК" })).toEqual({ carrier: "СДЭК", terms: "ДОСТАВКА ТК" });
    expect(headPatch(shipment(), { terms: "САМОВЫВОЗ" })).toEqual({ terms: "САМОВЫВОЗ", carrier: "Самовывоз" });
    expect(headPatch(shipment({ deadline: "2026-12-01" }), { deadline: "" })).toEqual({ deadline: null });
  });

  it("documents always resend all five values, the track is never dropped", () => {
    expect(documentsBody(shipment(), { phone: "1" })).toBeNull();
    expect(documentsBody(shipment(), { upd_number: "У-2" })).toEqual({
      version: 3, upd_type: "ЭДО", upd_number: "У-2", upd_date: "2026-11-20", upd_url: "/api/shipping/files/f1", track_number: "TRK-1",
    });
    // «Убрать файл» — пустая ссылка, остальное как было.
    expect(documentsBody(shipment(), { upd_url: "" })).toMatchObject({ upd_url: "", track_number: "TRK-1", upd_number: "У-1" });
    // Без УПД: № и дата уходят пустыми; тип из карточки проекта — null.
    expect(documentsBody(shipment(), { upd_type: "БЕЗ УПД" })).toMatchObject({ upd_type: "БЕЗ УПД", upd_number: "", upd_date: null });
    expect(documentsBody(shipment({ inherited: ["upd_type"] }), { track_number: "TRK-2" })).toMatchObject({ upd_type: null, track_number: "TRK-2" });
  });

  it.each([
    ["Новая", true, true, true], ["Упакована", true, true, true], ["Отгружена", false, true, false], ["Закрыта", false, false, false], ["Отменена", false, false, false],
  ])("status %s: head %s, documents %s, track %s (shipped track — history correction)", (status, head, documents, track) => {
    const keys = editableKeys(shipment({ status }), true);
    expect(keys.has("phone")).toBe(head);
    expect(keys.has("tk_note")).toBe(head);
    expect(keys.has("upd_number")).toBe(documents);
    expect(keys.has("track_number")).toBe(track);
    expect(editableKeys(shipment({ status }), false).size).toBe(0);
  });

  it("saves head → documents → carrier comment, each with the version from the previous answer", async () => {
    stub((url, body) => json({ shipment: shipment({ version: Number(body.version) + 1, ...(url.endsWith("/head") ? { phone: String(body.phone) } : {}),
      ...(url.endsWith("/documents") ? { upd_number: String(body.upd_number) } : {}), ...(url.endsWith("/tk-note") ? { tk_note: String(body.tk_note) } : {}) }) }));
    const saved = await saveShipment(shipment(), { phone: "+7 111", upd_number: "У-9", tk_note: " Хрупкое ", contact: "Иванов" });
    expect(calls.map((call) => [call.url.replace("/api/shipping/shipments/s1/", ""), call.method, call.body.version])).toEqual([
      ["head", "PUT", 3], ["documents", "PUT", 4], ["tk-note", "PUT", 5]]);
    expect(calls[0].body).toEqual({ version: 3, phone: "+7 111" });
    expect(calls[1].body).toMatchObject({ track_number: "TRK-1", upd_url: "/api/shipping/files/f1" });
    expect(calls[2].body).toEqual({ version: 5, tk_note: "Хрупкое" });
    // Экран получает отправку из последнего ответа.
    expect(saved).toMatchObject({ version: 6, tk_note: "Хрупкое" });
  });

  it("nothing changed — no request; a refusal after the head keeps the saved version for the screen", async () => {
    stub(() => json({}));
    expect(await saveShipment(shipment(), { phone: "+7 900" })).toMatchObject({ version: 3 });
    expect(calls).toEqual([]);
    stub((url, body) => url.endsWith("/head") ? json({ shipment: shipment({ version: 4, phone: String(body.phone) }) })
      : json({ detail: "Отгрузка изменилась после загрузки формы" }, 409));
    const failure = await saveShipment(shipment(), { phone: "1", upd_number: "У-9" }).catch((reason) => reason);
    expect(failure).toBeInstanceOf(PartialSaveError);
    expect(failure).toMatchObject({ saved: true, shipment: { version: 4, phone: "1" } });
  });
});
