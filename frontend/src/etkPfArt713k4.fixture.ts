// ЭТК 713К.4 КБ1 (прод, ревизия 17) для проверки картинок ПФ: конфигурация сборки — как её собирает генератор из
// снимка формы (`build_config` + `gen_state`), операции ЭТК — по строке генератора, с комплектующими трубок, как их
// кладёт сборка ЭТК. Группы ПФ — по схеме той же конфигурации.
import catalog from "./fixtures/connectorCatalog.json";
import golden from "./fixtures/gen713k4Rev17.json";
import { etkPfGroups, type EtkPfGroup } from "./etkRoute";
import { restoreFromSnapshot, snapshotFromConfig } from "./genBuildState";
import { ensureSeparateLengths, initState, makeGen, type Comp, type GenData } from "./tcGenLogic";
import { DSA } from "./tcGenRealHarness.fixture";

type Row = Record<string, unknown>;

/** Корпус разъёма, как его кладёт сборка ЭТК в операцию монтажа (`operation.housing`): из каталога серии; в каталоге
 *  нет корпуса с двумя контактами SMR/SMP и трёх контактов DS1071 — берётся корпус соседнего типоразмера той же серии. */
const HOUSING_BASE: Record<string, { base: string; cols: number; grid: number[][] }> = {
  "SMR-02V-B": { base: "SMR-04V-B", cols: 2, grid: [[1, 2]] },
  "SMP-02V-BC": { base: "SMP-04V-BC", cols: 2, grid: [[2, 1]] },
  "I-DS1071-SCB01X3": { base: "I-DS1071-SCB02X5", cols: 3, grid: [[1, 3, 5], [2, 4, 6]] },
  "I-DS1071-SCB02X3": { base: "I-DS1071-SCB02X5", cols: 3, grid: [[1, 3, 5], [2, 4, 6]] },
};
function housingFor(art: string): Row | null {
  const find = (name: string) => (catalog as Array<{ housing?: Row }>).find((item) => item.housing?.art === name)?.housing ?? null;
  const own = find(art);
  if (own) return own;
  const alias = HOUSING_BASE[art], base = alias && find(alias.base);
  return base ? { ...base, art, cols: alias.cols, n: alias.cols * (base.rows as number), grid: alias.grid } : null;
}

export type Etk713k4 = { document: Row; operations: Row[]; groups: EtkPfGroup[]; byName: (name: string) => EtkPfGroup };

export function etk713k4(): Etk713k4 {
  const data: GenData = { ...DSA, components: golden.components as Comp[] };
  const snap = snapshotFromConfig({ gen_state: golden.gen_state })!;
  const { S } = restoreFromSnapshot(data.components, initState(data), snap);
  const first = makeGen(data, ensureSeparateLengths(S));
  const stuck = first.stickTemplates();
  const config = makeGen(data, stuck ? { ...ensureSeparateLengths(S), selectedTemplates: stuck } : ensureSeparateLengths(S)).buildConfig() as Row & { ops: Row[]; route: Row };
  // Что кладёт сборка ЭТК в комплектующие операций с трубкой (прод: ТТК 3/1 на сростки, ТТК 12/4 на жгут).
  const tubes: Record<string, string> = { insSpl1: "ТТК 3/1мм, черная", insSpl2: "ТТК 3/1мм, черная", insSleeves: "ТТК 12/4мм, черная" };
  const operations = config.ops.map((op, index): Row => ({
    position: index + 1, type: op.type, code: String(op.opNum || ""), connector: (op.ep as Row | undefined)?.label ?? "",
    components: tubes[String(op.opKey)] ? [{ art: tubes[String(op.opKey)], norm: "0.02", unit: "М." }] : [],
    fields: { wires: op.wires ?? [] },
    ...(op.type === "insTerm" ? { housing: housingFor(String((op.ep as Row).connArt)) } : {}),
  }));
  const document: Row = { build_config: { ...config, gen_state: golden.gen_state }, operations };
  const groups = etkPfGroups(config.route as never, operations as never);
  return { document, operations, groups, byName: (name) => groups.find((group) => group.name.startsWith(name))! };
}

/** Входы ПФ из других ПФ — текстом стрелок, как их даёт схема (ТТК, нарезанные в «Резке трубок»). */
export const INPUTS_713K4: Record<string, string[]> = {
  "Пайка С1, С2": ["Провода нарезаны ×6", "ТТК 3/1мм, черная ×2"],
  "Пайка С3, С4": ["Провода нарезаны ×6", "ТТК 3/1мм, черная ×2"],
  "ТУТ на месте Б": ["Ветвь X1 ×1", "Ветвь X2 ×1", "Ветвь X3 ×1", "Ветвь X4 ×1", "ТТК 12/4мм, черная ×1"],
  "Кабельная сборка": ["ТУТ на месте Б ×1"],
};
