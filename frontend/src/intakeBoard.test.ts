import { describe, expect, it } from "vitest";
import { buildBoard, kbSummary, matchesFilter, NO_FILTER, shipmentBlocker, type BoardRights } from "./intakeBoard";
import type { DispatchShipment, IntakeRow } from "./productionIntakeApi";

const warehouse: BoardRights = { canManage: false, canWarehouse: true, canDispatch: true };
const manager: BoardRights = { canManage: true, canWarehouse: false, canDispatch: true };
const now = new Date(2026, 10, 9, 12);

const row = (over: Partial<IntakeRow> = {}) => ({
  project: "591К.3", customer: "Заказчик", deadline: "2026-11-12", item_index: "КБ1", name: "Жгут", unit: "шт", stage: "В работе", project_stage: "В работе",
  needed: "40", launched: "40", delivered: "0", accepted: "0", packed: "0", sent: "0", remaining: "40", good_id: "g1", good_version: 1, grn: null, location: "", tasks: [], ...over,
}) as IntakeRow;
const ship = (over: Partial<DispatchShipment> = {}) => ({
  id: "s1", number: "ОТГ-1", version: 1, created_at: "2026-11-05T09:00:00", status: "Упакована", customer: "К", receiver: "", shipping_point: "", contact: "", phone: "", terms: null, carrier: "",
  deadline: null, upd_type: null, upd_number: "", upd_date: null, upd_url: "", pl_number: "PL1-1", dn_number: "DN1-1", track_number: "", packed_at: null, shipped_at: null, manager: "", note: "", tk_note: "",
  places: 1, weight: "", boxes: [], inherited: [], missing_data: [], missing_ship: [], ready_to_ship: false,
  lines: [{ id: "l", good_id: "g", project: "828К.", item_index: "КБ3", article: "", name: "Жгут", requested: "1", picked: "1", shortage: "0", unit: "шт", grn: null }], ...over,
}) as unknown as DispatchShipment;

describe("kbSummary", () => {
  it("одна КБ — с именем, до трёх — через запятую, дальше «и ещё N»", () => {
    expect(kbSummary([{ item_index: "КБ3", name: "Жгут для ТМЛР.465484.016" }])).toBe("КБ3 Жгут для ТМЛР.465484.016");
    expect(kbSummary(["КБ1", "КБ2", "КБ3", "КБ4", "КБ5"].map((item_index) => ({ item_index, name: "Кабель" })))).toBe("КБ1 Кабель, КБ2 Кабель, КБ3 Кабель и ещё 2");
  });
});

describe("buildBoard", () => {
  it("стадия отправки по статусу и готовности; закрытая старше 30 дней — не на доске", () => {
    const cards = buildBoard({ rows: [], addressees: [], rights: warehouse, now, shipments: [
      ship({ id: "a" }), ship({ id: "b", ready_to_ship: true }), ship({ id: "c", status: "Собрана" }),
      ship({ id: "d", status: "Закрыта", shipped_at: "2026-11-01" }), ship({ id: "e", status: "Закрыта", shipped_at: "2026-09-01" }), ship({ id: "f", status: "Отменена" }),
    ] });
    expect(cards.map((card) => `${card.id}:${card.column}`).sort()).toEqual(["s:a:packed", "s:b:to_ship", "s:c:accepted", "s:d:shipped"]);
  });

  it("действие «Отгрузить»: склад — кнопка, менеджер — выключена с пояснением", () => {
    const input = { rows: [], addressees: [], now, shipments: [ship({ ready_to_ship: true })] };
    expect(buildBoard({ ...input, rights: warehouse })[0].action).toEqual({ id: "ship", label: "Отгрузить" });
    expect(buildBoard({ ...input, rights: manager })[0].action).toMatchObject({ id: "ship", disabled: true, note: "отгружает склад" });
  });

  it("порядок дел в «Упаковано»: PL → DN → УПД → трек → данные", () => {
    const action = (over: Partial<DispatchShipment>) => buildBoard({ rows: [], addressees: [], rights: warehouse, now, shipments: [ship(over)] })[0].action?.id;
    expect(action({ pl_number: "", missing_ship: ["тип УПД"] })).toBe("make-pl");
    expect(action({ dn_number: "", missing_ship: ["тип УПД"] })).toBe("make-dn");
    expect(action({ missing_ship: ["№ УПД", "трек-номер"] })).toBe("enter-upd-number");
    expect(action({ terms: "ДОСТАВКА ТК", carrier: "СДЭК", missing_ship: ["трек-номер"], ready_for_track: true })).toBe("enter-track");
    expect(action({ missing_ship: ["адрес"] })).toBe("fill");
  });

  it("«Принято»: нет разрешения — блокер и кнопка менеджера; есть — «Упаковать»", () => {
    const rows = [row({ item_index: "КБ1", accepted: "5", remaining: "0", pack_permission_remaining: "5" }), row({ item_index: "КБ2", accepted: "5", remaining: "0" })];
    const [asWarehouse] = buildBoard({ rows, shipments: [], addressees: [], rights: warehouse, now }).filter((card) => card.column === "accepted");
    expect(asWarehouse.action).toEqual({ id: "pack", label: "Упаковать КБ1…" });
    expect(asWarehouse.blocker).toEqual({ text: "КБ2: нет разрешения упаковки", who: "даёт менеджер (раздел «Отгрузка»)" });
    const [asManager] = buildBoard({ rows, shipments: [], addressees: [], rights: manager, now }).filter((card) => card.column === "accepted");
    expect(asManager.action).toEqual({ id: "pack", label: "Упаковать КБ1…" });
    expect(asManager.extra).toEqual([{ id: "permit", label: "Разрешить упаковку…" }]);
  });

  it("«Отгружено»: незакрытая называет, чего нет, и ведёт к недостающему; закрытая — «закрыта»", () => {
    const [open] = buildBoard({ rows: [], addressees: [], rights: warehouse, now, shipments: [ship({ status: "Отгружена", shipped_at: "2026-11-08", missing_ship: ["файл УПД"], upd_type: "БУМАГА" })] });
    expect(open.blocker).toEqual({ text: "не закрыта — нет: файл УПД", who: "закроется сама: вносит склад или менеджер" });
    expect(open.action).toEqual({ id: "enter-upd-file", label: "Загрузить УПД…" });
    const [closed] = buildBoard({ rows: [], addressees: [], rights: warehouse, now, shipments: [ship({ status: "Закрыта", shipped_at: "2026-11-08" })] });
    expect(closed.okText).toBe("закрыта");
    expect(closed.action).toBeNull();
  });
});

describe("shipmentBlocker и фильтры", () => {
  it("называет, кто вносит: трек — склад, без УПД — ПИН", () => {
    expect(shipmentBlocker(ship({ missing_ship: ["трек-номер"] }))).toEqual({ text: "нет: трек-номер", who: "вписывает склад после УПД и ТК" });
    expect(shipmentBlocker(ship({ missing_ship: ["разрешение на отгрузку без УПД"] }))?.who).toBe("разрешает уполномоченный по ПИН");
    expect(shipmentBlocker(ship())).toBeNull();
  });

  it("поиск по словам, «Срок горит» и «Заказчик»", () => {
    const [card] = buildBoard({ rows: [], addressees: [], rights: warehouse, now, shipments: [ship({ deadline: "2026-11-11", track_number: "TRK-77" })] });
    expect(matchesFilter(card, { ...NO_FILTER, query: "trk-77 pl1" }, now)).toBe(true);
    expect(matchesFilter(card, { ...NO_FILTER, query: "нет-такого" }, now)).toBe(false);
    expect(matchesFilter(card, { ...NO_FILTER, hot: true }, now)).toBe(true);
    expect(matchesFilter({ ...card, deadline: "2026-12-30" }, { ...NO_FILTER, hot: true }, now)).toBe(false);
    expect(matchesFilter(card, { ...NO_FILTER, customer: "Другой" }, now)).toBe(false);
  });
});
