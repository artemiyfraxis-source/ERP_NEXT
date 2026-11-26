import { useEffect, useState } from 'react';
import { ApiError, failureText, warehouseApi, type WarehouseLot } from './api';
import { DataTable, type DataColumn } from './DataTable';
import { PageWindow } from './PageWindow';
import { LotLabel } from './LabelPrint';
import { QualityChip } from './GrnFields';
import { useCanAct } from './rights';
import type { MaterialOwner, QualityState } from './grn';
export function MaterialOwners({readOnly = false}: {readOnly?: boolean}) {
  const [owners,setOwners]=useState<MaterialOwner[]|null>(null);
  const [error,setError]=useState(''); const [busy,setBusy]=useState(false);
  const [name,setName]=useState(''); const [identity,setIdentity]=useState('');
  useEffect(()=>{let live=true;warehouseApi.materialOwners().then(result=>{if(live)setOwners(result.owners);},reason=>{if(live)setError(failureText(reason,'Владельцы не загрузились'));});return()=>{live=false;};},[]);
  const columns: DataColumn<MaterialOwner>[]=[{key:'name',title:'Владелец материала',value:owner=>owner.display_name,grow:true},{key:'identity',title:'Юридический идентификатор',value:owner=>owner.legal_identity}];
  async function add(){setBusy(true);setError('');try{const result=await warehouseApi.createMaterialOwner(name.trim(),identity.trim());setOwners(current=>[...(current??[]).filter(owner=>owner.id!==result.owner.id),result.owner]);setName('');setIdentity('');}catch(reason){setError(failureText(reason,'Владелец не добавлен'));}finally{setBusy(false);}}
  return <section className="panel" aria-label="Владельцы материалов"><h2>Владельцы материалов</h2>
    {error&&<p className="error-banner" role="alert">{error}</p>}
    {!readOnly&&<form className="grn-owner-form" onSubmit={event=>{event.preventDefault();void add();}}>
      <label>Название владельца *<input required maxLength={512} value={name} onChange={event=>setName(event.target.value)} disabled={busy}/></label>
      <label>ИНН / юридический идентификатор *<input required maxLength={512} placeholder="ИНН:7707083893" value={identity} onChange={event=>setIdentity(event.target.value)} disabled={busy}/></label>
      <button className="primary small" disabled={busy||!name.trim()||!identity.trim()}>Добавить владельца</button>
    </form>}
    {owners ? <DataTable rows={owners} columns={columns} rowKey={owner=>owner.id} emptyText="Владельцев пока нет — добавьте первого владельца."/> : !error&&<p className="empty">Загрузка…</p>}
  </section>;
}
export function LotQuality({lot,onChanged}: {lot: WarehouseLot;onChanged:()=>void}) {
  const canDecide=useCanAct('quality');
  const [open,setOpen]=useState(false); const [state,setState]=useState<QualityState>('RELEASED');
  const [basis,setBasis]=useState('');const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [changed,setChanged]=useState(false);const [stale,setStale]=useState(false);
  const current = lot.grn_contract_version===1 && lot.quality_version != null;
  async function save(){setBusy(true);setError('');try{await warehouseApi.decideQuality(lot,state,basis.trim());setChanged(true);}catch(reason){setError(failureText(reason,'Решение не сохранено'));setStale(reason instanceof ApiError && reason.status===409);}finally{setBusy(false);}}
  const close=()=>{setOpen(false);if(changed)onChanged();};
  return <><QualityChip passport={lot} quietLegacy/>{canDecide&&current&&<button type="button" className="secondary small" onClick={()=>setOpen(true)}>Решение ОТК…</button>}
    {open&&<PageWindow label="Решение ОТК по месту" width={640} close={close}><section className="panel">
      <h2>Решение ОТК по всему месту</h2><p>ГРН {lot.grn}{lot.package?` / ${lot.package}`:''} · {lot.balance} {lot.unit} · {lot.storage_location||'место не указано'}</p>
      {error&&<p className="error-banner" role="alert">{error}{stale&&<button type="button" className="secondary small" onClick={()=>{setOpen(false);onChanged();}}>Обновить место</button>}</p>}
      {changed?<><p className="success-banner" role="status">Решение ОТК сохранено — перепечатайте этикетку места.</p><LotLabel lotId={lot.id}/><button className="secondary" onClick={close}>Закрыть</button></>:<form className="grn-quality-form" onSubmit={event=>{event.preventDefault();void save();}}>
        <label>Решение<select value={state} onChange={event=>setState(event.target.value as QualityState)} disabled={busy}><option value="RELEASED">ГОДЕН ✓ — освободить всё место</option><option value="QTN">КАРАНТИН</option><option value="REJECTED">НЕ ГОДЕН</option>{['GOS','PAM'].includes(lot.grn_category??'')&&<option value="NOT_REQUIRED">ВК не требуется</option>}</select></label>
        <label>Основание решения *<textarea required maxLength={2000} value={basis} onChange={event=>setBasis(event.target.value)} disabled={busy}/></label>
        <div className="header-actions"><button className="primary" disabled={busy||stale||!basis.trim()}>Сохранить решение по всему месту</button><button type="button" className="secondary" disabled={busy} onClick={close}>Отмена</button></div>
      </form>}
    </section></PageWindow>}
  </>;
}
