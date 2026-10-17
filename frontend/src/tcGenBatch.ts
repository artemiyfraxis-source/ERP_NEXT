/*
 * Генератор техкарт без формы — для пакетной генерации по всем проектам (backend `app.workflows.techcards_all`).
 * Делает то же, что TcGenerator при открытии сборки и нажатии «Создать» (SUPPLYMAP «Создать техкарты»): справочники как loadRefs,
 * состав как GET /api/techcards/kitsets/{project}/bom/{index}, засев формы initState, «залипание» шаблонов
 * (stickTemplates, как эффект renderOpsCard) и validate. Выход — тот же config, что форма отдаёт в
 * POST /api/techcards/projects/{id}/build. Отдельной логики здесь нет: правила живут в tcGenLogic.ts.
 */
import {
  coaxDiaFromRecords,
  coaxFromFields,
  initState,
  makeGen,
  opFromFields,
  terFromFields,
  wireDiaFromRecords,
  type CalcOp,
  type Comp,
  type GenData,
  type Tmpl,
} from "./tcGenLogic";

type Fields = Record<string, string>;

/** Справочники генератора в виде ответов API: шаблоны техкарт, записи БД.ОП / БД.ТЕР / БД.КОАКС / СПР.КАБ · коаксиал и
 *  строки «Ø изоляции по маркам» из базы монтажных проводов (GET /api/references/wire-dia, backend references.wire_dia). */
export type BatchRefs = {
  templates: Array<{ id: string; title: string; category: string }>;
  op: Fields[];
  ter: Array<{ id: string; fields: Fields }>;
  coax: Fields[];
  wire_dia: Fields[];
  kab_coax: Fields[];
};

/** Состав сборки — ответ GET /api/techcards/kitsets/{project}/bom/{index}. */
export type BatchBom = {
  index: string;
  name: string;
  run: string;
  part_qty: string;
  components: Array<{ id: string; type: string; name: string; art: string; qty: number | string }>;
  calc_ops: CalcOp[];
};

export type BatchInput = { refs: BatchRefs; items: Array<{ key: string; bom: BatchBom }> };
export type BatchResult = { key: string; config: Record<string, unknown> | null; error: string; warnings: string[] };

type Refs = Pick<GenData, "templates" | "ops" | "terRecords" | "coaxRecords" | "wireDia" | "coaxCableDia">;

/** loadRefs (TcGenerator.tsx) над уже прочитанными записями. */
export function refsFromRecords(refs: BatchRefs): Refs {
  return {
    templates: refs.templates.map((item): Tmpl => ({ id: item.id, title: item.title, category: item.category })),
    ops: refs.op.map(opFromFields),
    terRecords: refs.ter.map((record) => terFromFields(record.id, record.fields)).filter((record) => record.article),
    coaxRecords: refs.coax.map(coaxFromFields),
    wireDia: wireDiaFromRecords(refs.wire_dia),
    coaxCableDia: coaxDiaFromRecords(refs.kab_coax),
  };
}

/** Данные формы `D` так, как их собирает TcGenerator из справочников и состава сборки. */
export function generatorData(refs: Refs, bom: BatchBom): GenData {
  const components = bom.components.map((item): Comp => ({
    id: item.id, type: item.type || "", name: item.name || "", art: item.art || "", qty: item.qty,
  }));
  return {
    ...refs,
    components,
    assemblyInfo: { "Индекс": bom.index || "", "Наименование": bom.name || "", "Кол-во": bom.run || "" },
    partQty: Number(bom.part_qty) || 1,
    calcOps: bom.calc_ops || [],
  };
}

/** Форма, открытая на сборке и ни разу не тронутая: засев, залипание шаблонов, «Создать техкарты». */
export function autoConfig(D: GenData): { config: Record<string, unknown> | null; error: string; warnings: string[] } {
  let S = initState(D);
  for (let pass = 0; pass < 10; pass++) {
    const next = makeGen(D, S).stickTemplates();
    if (!next) break;
    S = { ...S, selectedTemplates: next };
  }
  const { cfg, error, warnings } = makeGen(D, S).validate();
  return error ? { config: null, error, warnings: [] } : { config: cfg, error: "", warnings };
}

export function runBatch(input: BatchInput): { items: BatchResult[] } {
  const refs = refsFromRecords(input.refs);
  return {
    items: input.items.map(({ key, bom }) => {
      try {
        return { key, ...autoConfig(generatorData(refs, bom)) };
      } catch (reason) {
        return { key, config: null, error: `генератор не справился: ${reason instanceof Error ? reason.message : String(reason)}`, warnings: [] };
      }
    }),
  };
}
