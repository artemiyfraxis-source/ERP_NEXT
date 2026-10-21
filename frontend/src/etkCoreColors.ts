// Цвета жил многожильного кабеля в операциях монтажа ЭТК (владелец 19.10, 545К.9: «цвета задавал», а эскиз серый).
// Цвета лежат в `build_config` документа: `wires[].coreColors` (по жилам кабеля) и `pinout.cores` — оттуда же их берёт
// «Схема связей» (etkLinkScheme). Операция монтажа знает только провод и его контакты (`fields.wires[].pins`), поэтому при
// чтении документа каждому проводу операции добавляется `coreColors` (названия) и `coreHex` (готовые цвета, свои — по
// `build_config.colorHex`): жила k сидит в контакте pins[k]. Выданные карты показывают цвета без пересборки.
import { coreColor } from "./pinoutCheck";
import { setCustomColors } from "./wireColors";

type Row = Record<string, unknown>;

const obj = (value: unknown): Row => (value && typeof value === "object" && !Array.isArray(value) ? value as Row : {});
const list = (value: unknown): Row[] => (Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Row[] : []);
const text = (value: unknown): string => (value == null ? "" : String(value).trim());
const key = (wire: Row): string => text(wire.art) || text(wire.name);

/** Операции документа, у проводов которых есть цвета жил из `build_config`; без цветов — те же объекты. */
export function withCoreColors<T>(operations: T[], document: unknown): T[] {
  const config = obj(obj(document).build_config);
  const cables = list(config.wires), cores = list(obj(config.pinout).cores);
  if (!cables.length || !operations.length) return operations;
  setCustomColors(config.colorHex as Record<string, string> | undefined);
  const colorsOf = (pos: number): string[] => {
    const own = Array.isArray(cables[pos]?.coreColors) ? (cables[pos].coreColors as unknown[]).map(text) : [];
    const size = Math.max(own.length, ...cores.filter((item) => Number(item.pos) === pos).map((item) => Number(item.unit) + 1), 0);
    return Array.from({ length: size }, (_, k) => own[k] || text(cores.find((item) => Number(item.pos) === pos && Number(item.unit) === k)?.color));
  };
  return operations.map((operation) => {
    const fields = obj(obj(operation).fields), wires = list(fields.wires);
    if (!wires.length) return operation;
    const seen = new Map<string, number>();
    let changed = false;
    const next = wires.map((wire) => {
      const name = key(wire), nth = seen.get(name) ?? 0;
      seen.set(name, nth + 1);
      const matches = cables.map((cable, pos) => ({ cable, pos })).filter((item) => key(item.cable) === name);
      const pos = (matches[nth] ?? matches[0])?.pos;
      const colors = pos == null ? [] : colorsOf(pos);
      if (!colors.some(Boolean) || !Array.isArray(wire.pins)) return wire;
      changed = true;
      return { ...wire, coreColors: colors, coreHex: colors.map((color) => coreColor(color || undefined)?.hex ?? "") };
    });
    return changed ? { ...operation, fields: { ...fields, wires: next } } : operation;
  });
}

/** Цвет жилы в контакте `pin` провода `wire` — hex; жилы без своего цвета и провода без цветов — "" (красят по наименованию). */
export function pinCoreColor(wire: Row, pin: number): string {
  const pins = Array.isArray(wire.pins) ? wire.pins as number[] : [], hex = Array.isArray(wire.coreHex) ? wire.coreHex as unknown[] : [];
  const at = pins.indexOf(pin);
  return at >= 0 ? text(hex[at]) : "";
}

/** Название цвета жилы в контакте `pin` («красный»); "" — цвета нет. */
export function pinCoreName(wire: Row, pin: number): string {
  const pins = Array.isArray(wire.pins) ? wire.pins as number[] : [], names = Array.isArray(wire.coreColors) ? wire.coreColors as unknown[] : [];
  const at = pins.indexOf(pin);
  return at >= 0 ? text(names[at]) : "";
}
