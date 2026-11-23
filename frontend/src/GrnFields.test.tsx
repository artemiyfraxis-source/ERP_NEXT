import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { GrnFields, QualityChip } from './GrnFields';
import { MaterialOwners, LotQuality } from './MaterialOwners';
import { GRN_CATEGORIES, grnError, type GrnInput } from './grn';
import { setRightsPrincipal } from './rights';
import type { Principal, WarehouseLot } from './api';
const json = (body:unknown,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
afterEach(()=>{cleanup();vi.restoreAllMocks();setRightsPrincipal(null);});
function Editor(){const [value,setValue]=useState<GrnInput>({});return <><GrnFields value={value} onChange={setValue}/><output>{JSON.stringify(value)}</output></>;}
it('requires an explicit choice and clears the previous ownership context on category change',async()=>{
 vi.spyOn(globalThis,'fetch').mockResolvedValue(json({owners:[{id:'owner',display_name:'Заказчик',legal_identity:'TEST:1'}]}));
 render(<Editor/>);const category=screen.getByLabelText('Категория ГРН');expect(category).toHaveValue('');
 expect(screen.getAllByRole('option')).toHaveLength(GRN_CATEGORIES.length+1);
 fireEvent.change(category,{target:{value:'CSM'}});
 await screen.findByRole('option',{name:'Заказчик · TEST:1'});
 fireEvent.change(screen.getByLabelText('Владелец материала *'),{target:{value:'owner'}});
 fireEvent.change(screen.getByLabelText('Проект / заказ клиента *'),{target:{value:'591К.3'}});
 fireEvent.change(screen.getByLabelText('Документ передачи *'),{target:{value:'М-15 №1'}});
 expect(screen.getByRole('status')).toHaveTextContent('owner');
 fireEvent.change(category,{target:{value:'PPM'}});
 expect(screen.queryByLabelText('Владелец материала *')).toBeNull();
 expect(screen.getByRole('status')).not.toHaveTextContent('owner');
});
it('keeps a failed owner lookup visible',async()=>{
 vi.spyOn(globalThis,'fetch').mockResolvedValue(json({detail:'Нет связи с реестром'},503));
 render(<GrnFields value={{grn_category:'CSM'}} onChange={()=>{}}/>);
 expect(await screen.findByRole('alert')).toHaveTextContent('Нет связи с реестром');
});
it.each([
 [{grn_category:'CSM',grn_context:{owner_id:'o',customer_order:'p'}},true],
 [{grn_category:'SUB',grn_context:{subcontractor:'s',production_order:'p',acceptance_document:'a'}},false],
 [{grn_category:'PAM',grn_context:{equipment:'Станок'}},false],
 [{grn_category:'R&D',grn_context:{project:'p'}},true],
] as [GrnInput,boolean][])('validates category context without inventing defaults: %j',(input,invalid)=>expect(Boolean(grnError(input))).toBe(invalid));
it('does not turn legacy or expired quality into a green pass',()=>{
 const {rerender}=render(<QualityChip passport={{}}/>);expect(screen.getByText('ВК не указан')).toBeInTheDocument();
 rerender(<QualityChip passport={{grn_contract_version:1,quality_state:'RELEASED',expiry_date:'2001-01-01'}}/>);
 expect(screen.getByText('Срок истёк — удержание')).toHaveAttribute('data-tone','danger');
 rerender(<QualityChip passport={{grn_contract_version:1,quality_state:'RELEASED',expiry_required:true}}/>);
 expect(screen.queryByText('ГОДЕН ✓')).toBeNull();expect(screen.getByText('Срок не указан — удержание')).toBeInTheDocument();
});
it('loads and adds owners through the registry, retaining server errors',async()=>{
 const request=vi.spyOn(globalThis,'fetch').mockImplementation(async(_url,init)=>init?.method==='POST'?json({owner:{id:'o',display_name:'Заказчик',legal_identity:'TEST:1'}}):json({owners:[]}));
 render(<MaterialOwners/>);await screen.findByText('Владельцев пока нет — добавьте первого владельца.');
 fireEvent.change(screen.getByLabelText('Название владельца *'),{target:{value:'Заказчик'}});
 fireEvent.change(screen.getByLabelText('ИНН / юридический идентификатор *'),{target:{value:'TEST:1'}});
 fireEvent.click(screen.getByRole('button',{name:'Добавить владельца'}));
 expect(await screen.findByText('TEST:1')).toBeInTheDocument();
 const sent=request.mock.calls.find(([,init])=>init?.method==='POST')!;
 expect(JSON.parse(String(sent[1]?.body))).toEqual({display_name:'Заказчик',legal_identity:'TEST:1'});
});
const lot={id:'lot',version:8,quality_version:3,grn_contract_version:1,grn_category:'PPM',quality_state:'IQH',grn:'PPM.26.000001',balance:'10',unit:'шт',package:'П1',storage_location:'1F4'} as WarehouseLot;
it('sends whole-place decisions with both versions, then offers relabeling',async()=>{
 const request=vi.spyOn(globalThis,'fetch').mockResolvedValue(json({lot:{...lot,quality_state:'RELEASED'}}));
 const reload=vi.fn();render(<LotQuality lot={lot} onChanged={reload}/>);
 fireEvent.click(screen.getByRole('button',{name:'Решение ОТК…'}));
 expect(screen.queryByLabelText('Количество')).toBeNull();
 fireEvent.change(screen.getByLabelText('Основание решения *'),{target:{value:'Акт ВК №1'}});
 fireEvent.click(screen.getByRole('button',{name:'Сохранить решение по всему месту'}));
 await screen.findByText('Решение ОТК сохранено — перепечатайте этикетку места.');
 expect(JSON.parse(String(request.mock.calls[0][1]?.body))).toEqual({version:8,quality_version:3,state:'RELEASED',basis:'Акт ВК №1'});
 fireEvent.click(screen.getByRole('button',{name:'Закрыть'}));await waitFor(()=>expect(reload).toHaveBeenCalledOnce());
});
it('does not expose QC decisions to a warehouse-only principal',()=>{
 setRightsPrincipal({user:{id:'u'},roles:[],access_level:'operator',permissions:{warehouse:'edit'}} as unknown as Principal);
 render(<LotQuality lot={lot} onChanged={()=>{}}/>);expect(screen.queryByRole('button',{name:'Решение ОТК…'})).toBeNull();
});
it('a stale decision requires refreshing the place before another submit',async()=>{
 vi.spyOn(globalThis,'fetch').mockResolvedValue(json({detail:'Место изменено'},409));const reload=vi.fn();
 render(<LotQuality lot={lot} onChanged={reload}/>);fireEvent.click(screen.getByRole('button',{name:'Решение ОТК…'}));
 fireEvent.change(screen.getByLabelText('Основание решения *'),{target:{value:'Акт'}});fireEvent.click(screen.getByRole('button',{name:'Сохранить решение по всему месту'}));
 await screen.findByText('Место изменено');expect(screen.getByRole('button',{name:'Сохранить решение по всему месту'})).toBeDisabled();
 fireEvent.click(screen.getByRole('button',{name:'Обновить место'}));expect(reload).toHaveBeenCalledOnce();
});
