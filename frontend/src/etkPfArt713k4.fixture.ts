// ЭТК 713К.4 КБ1 (прод, ревизия 17) для проверки картинок ПФ: конфигурация сборки — как её собирает генератор из
// снимка формы (`build_config` + `gen_state`), операции ЭТК — по строке генератора, с комплектующими трубок, как их
// кладёт сборка ЭТК. Группы ПФ — по схеме той же конфигурации.
import golden from "./fixtures/gen713k4Rev17.json";
import { etkPfGroups, type EtkPfGroup } from "./etkRoute";
import { restoreFromSnapshot, snapshotFromConfig } from "./genBuildState";
import { ensureSeparateLengths, initState, makeGen, type Comp, type GenData } from "./tcGenLogic";
import { DSA } from "./tcGenRealHarness.fixture";

type Row = Record<string, unknown>;

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
