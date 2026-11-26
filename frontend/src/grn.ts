export const GRN_CATEGORIES = [
  ['PPM', 'Закупные производственные материалы'], ['PAM', 'Вспомогательные материалы'],
  ['GOS', 'Хозяйственные материалы'], ['SUB', 'Изделия подрядчика'],
  ['CSM', 'Давальческие материалы'], ['FRX', 'Собственная продукция'], ['R&D', 'Опытная разработка'],
] as const;
export type GrnCategory = typeof GRN_CATEGORIES[number][0];
export type QualityState = 'IQH' | 'QTN' | 'RELEASED' | 'REJECTED' | 'NOT_REQUIRED';
export type GrnContext = Partial<Record<'owner_id' | 'customer_order' | 'transfer_document' | 'project' | 'production_order' | 'subcontractor' | 'acceptance_document' | 'purpose' | 'equipment', string | null>>;
export type GrnInput = { grn_category?: GrnCategory | null; grn_context?: GrnContext | null; expiry_required?: boolean; quality_state?: QualityState | null; quality_basis?: string | null };
export type GrnPassport = GrnInput & { grn_id?: string | null; grn_contract_version?: number | null; material_owner_id?: string | null; quality_version?: number | null; quality_decided_by?: string | null; quality_decided_at?: string | null };
export type MaterialOwner = { id: string; display_name: string; legal_identity: string };
export const categoryName = (code?: string | null) => GRN_CATEGORIES.find(([key]) => key === code)?.[1] ?? 'Категория не указана';
export function grnError(input: GrnInput): string {
  const context = input.grn_context ?? {};
  const has = (key: keyof GrnContext) => Boolean(context[key]?.trim());
  if (!input.grn_category) return 'Выберите категорию ГРН';
  const required: Partial<Record<GrnCategory, (keyof GrnContext)[]>> = {
    GOS: ['purpose'], SUB: ['subcontractor', 'production_order', 'acceptance_document'],
    CSM: ['owner_id', 'customer_order', 'transfer_document'], FRX: ['production_order', 'acceptance_document'], 'R&D': ['project', 'purpose'],
  };
  if (input.grn_category === 'PAM' && !has('purpose') && !has('equipment')) return 'Укажите назначение или оборудование';
  if (required[input.grn_category]?.some(key => !has(key))) return 'Заполните обязательные поля категории ГРН';
  if (input.quality_state && ['RELEASED','REJECTED','NOT_REQUIRED'].includes(input.quality_state) && !input.quality_basis?.trim()) return 'Укажите основание решения ОТК';
  return '';
}
