import { useEffect, useState } from 'react';
import { api, failureText } from './api';
import { useCanAct } from "./rights";
import { StatusChip } from './StatusChip';
import { GRN_CATEGORIES, type GrnContext, type GrnInput, type GrnPassport, type MaterialOwner } from './grn';
import './grn.css';
/** ВК state of a GRN passport. `quietLegacy`: in lists a historical row (before the 23.11 contract) shows nothing
 *  instead of «ВК не указан» on every old line; its quality stays unknown, never a pass. */
export function QualityChip({ passport, quietLegacy = false }: { passport: GrnPassport & {expiry_date?: string | null}; quietLegacy?: boolean }) {
  if (quietLegacy && passport.grn_contract_version !== 1 && !passport.quality_state) return null;
  const day = new Date().toLocaleDateString("sv-SE", {timeZone: "Europe/Moscow"});
  if (passport.grn_contract_version === 1 && passport.expiry_date && passport.expiry_date < day) return <StatusChip tone="danger">Срок истёк — удержание</StatusChip>;
  if (passport.grn_contract_version === 1 && passport.expiry_required && !passport.expiry_date) return <StatusChip tone="warning">Срок не указан — удержание</StatusChip>;
  const states = { IQH: ['Ожидает ВК', 'warning'], QTN: ['КАРАНТИН', 'warning'], RELEASED: ['ГОДЕН ✓', 'success'], REJECTED: ['НЕ ГОДЕН', 'danger'], NOT_REQUIRED: ['ВК не требуется', 'neutral'] } as const;
  const state = passport.quality_state ? states[passport.quality_state] : null;
  return <StatusChip tone={state?.[1] ?? 'neutral'} title={passport.quality_basis ?? undefined}>{state?.[0] ?? 'ВК не указан'}</StatusChip>;
}
export function GrnFields({ value, onChange, disabled = false, locked = false, quality = false }: {
  value: GrnInput; onChange: (next: GrnInput) => void; disabled?: boolean; locked?: boolean; quality?: boolean;
}) {
  const canQuality = useCanAct('quality');
  const [owners, setOwners] = useState<MaterialOwner[]>([]);
  const [ownerError, setOwnerError] = useState('');
  const [ownersLoading, setOwnersLoading] = useState(false);
  const category = value.grn_category ?? '';
  useEffect(() => {
    if (category !== 'CSM') return;
    let live = true; setOwnersLoading(true); setOwnerError('');
    api<{owners: MaterialOwner[]}>('/api/stock/material-owners').then(result => { if(live) setOwners(result.owners); }, reason => { if(live) setOwnerError(failureText(reason, 'Владельцы не загрузились')); }).finally(() => { if(live) setOwnersLoading(false); });
    return () => { live = false; };
  }, [category]);
  const context = value.grn_context ?? {};
  const set = (key: keyof GrnContext, text: string) => onChange({ ...value, grn_context: {...context, [key]: text} });
  const input = (key: keyof GrnContext, label: string, required = true) => <label key={key}>{label}{required ? ' *' : ''}<input maxLength={512} required={required} disabled={disabled || locked} value={context[key] ?? ''} onChange={event => set(key, event.target.value)}/></label>;
  return <div className="grn-fields">
    <label>Категория ГРН *<select aria-label="Категория ГРН" required disabled={disabled || locked} value={category} onChange={event => onChange({ grn_category: event.target.value as GrnInput['grn_category'], grn_context: {}, expiry_required: value.expiry_required })}>
      <option value="">Выберите категорию</option>{GRN_CATEGORIES.map(([code,name]) => <option key={code} value={code}>{name} · {code}</option>)}
    </select></label>
    {category === 'PPM' && input('project', 'Проект', false)}
    {category === 'PAM' && <>{input('purpose','Назначение',false)}{input('equipment','Оборудование',false)}</>}
    {category === 'GOS' && input('purpose','Назначение / подразделение')}
    {category === 'CSM' && <><label>Владелец материала *<select required disabled={disabled || locked || ownersLoading} value={context.owner_id ?? ''} onChange={event => set('owner_id',event.target.value)}>
      <option value="">{ownersLoading ? 'Загрузка владельцев…' : 'Выберите владельца'}</option>{owners.map(owner => <option key={owner.id} value={owner.id}>{owner.display_name} · {owner.legal_identity}</option>)}
    </select></label>{input('customer_order','Проект / заказ клиента')}{input('transfer_document','Документ передачи')}{ownerError && <p className="error-banner" role="alert">{ownerError}</p>}{!ownersLoading && !ownerError && !owners.length && <p className="warning-inline">Добавьте владельца в реестр «Владельцы материалов» на складе.</p>}</>}
    {category === 'SUB' && <>{input('subcontractor','Подрядчик')}{input('production_order','Производственный заказ')}{input('acceptance_document','Основание приёмки')}</>}
    {category === 'FRX' && <>{input('project','Проект',false)}{input('production_order','Производственный заказ')}{input('acceptance_document','Акт сдачи')}</>}
    {category === 'R&D' && <>{input('project','Проект разработки')}{input('purpose','Назначение')}</>}
    <label className="check-field"><input type="checkbox" checked={value.expiry_required ?? false} disabled={disabled || locked} onChange={event => onChange({...value, expiry_required:event.target.checked})}/>Срок годности обязателен</label>
    {quality && <><label>Решение о качестве<select disabled={disabled} value={value.quality_state ?? ''} onChange={event => onChange({...value,quality_state:(event.target.value || null) as GrnInput['quality_state']})}>
      <option value="">По результатам ВК</option><option value="IQH">Ожидает ВК</option><option value="QTN">КАРАНТИН</option><option value="RELEASED" disabled={!canQuality}>ГОДЕН ✓</option><option value="REJECTED" disabled={!canQuality}>НЕ ГОДЕН</option>{(category==='GOS'||category==='PAM') && <option value="NOT_REQUIRED">ВК не требуется</option>}
    </select></label>{value.quality_state && ['RELEASED','REJECTED','NOT_REQUIRED'].includes(value.quality_state) && <label>Основание решения *<input required maxLength={2000} disabled={disabled} value={value.quality_basis ?? ''} onChange={event => onChange({...value,quality_basis:event.target.value})}/></label>}</>}
  </div>;
}
