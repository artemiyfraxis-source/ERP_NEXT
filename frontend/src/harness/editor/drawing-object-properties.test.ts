// @vitest-environment node
import {expect,it,vi,afterEach} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {createElement,isValidElement,type ReactElement,type ReactNode} from "react";
import {PhysicalTopologyPanel} from "./PhysicalTopologyPanel";
import {PhysicalCoveringsPanel} from "./PhysicalCoveringsPanel";
import {DrawingObjectProperties,ContactSideViewCard} from "./DrawingObjectProperties";
import {DrawingScaleControl} from "./DrawingScaleControl";
import {CanvasObjectHint,CANVAS_HINT_DELAY} from "./CanvasObjectHint";
import {canvasPopoverPosition,CanvasObjectPopover} from "./CanvasObjectPopover";
import {physicalFixture} from "./physical-topology-fixture";
import {applyEditorCommand,type EditorCommand} from "./commands";

const hooks=vi.hoisted(()=>({effects:[] as (()=>unknown)[],set:vi.fn()}));
vi.mock("react",async original=>({...await original<typeof import("react")>(),useRef:(current:unknown)=>({current}),useState:(value:unknown)=>[value,hooks.set],useEffect:(effect:()=>unknown)=>hooks.effects.push(effect),useLayoutEffect:()=>{}}));
afterEach(()=>{vi.useRealTimers();hooks.effects=[];hooks.set.mockClear();});
function elements(node:ReactNode):ReactElement<Record<string,any>>[]{
  if(Array.isArray(node))return node.flatMap(elements);
  if(!isValidElement<Record<string,any>>(node))return [];
  return [node,...elements(node.props.children)];
}
it("hides topology inventories on the right but keeps creation and routing actions",()=>{
  const markup=renderToStaticMarkup(createElement(PhysicalTopologyPanel,{mode:"actions",document:physicalFixture(),selectedIds:["S0"],selectedId:"S0",onChange:()=>true,onSelect:()=>{}}));
  expect(markup).toContain("Распределить по Э4");expect(markup).toContain("Ещё выход +");
  expect(markup).not.toContain('<table');expect(markup).not.toContain('Ширина канала');expect(markup).not.toContain('Ручной маршрут проводов');
});
it("edits a pipe through its existing command without exposing unrelated pipe rows",()=>{
  const d=physicalFixture();let changed=d;
  const tree=PhysicalTopologyPanel({mode:"object",document:d,selectedId:"S0",selectedIds:["S0"],onSelect:vi.fn(),onChange:topology=>{changed=applyEditorCommand(d,{type:"set-physical-topology",topology});return true;}});
  const field=elements(tree).find(e=>e.props['aria-label']==="Ширина канала")!;
  field.props.onValueChange(1.5);
  expect(changed.physicalTopology!.segments[0]!.width).toBe(1.5);
  expect(changed.physicalTopology!.segments.slice(1)).toEqual(d.physicalTopology!.segments.slice(1));
  expect(changed.wires).toEqual(d.wires);
  expect(elements(tree).some(e=>e.type==="table")).toBe(false);
});
it("keeps pipe transparency out of individual pipe properties",()=>{
  const d=physicalFixture();let changed=d;
  const tree=PhysicalTopologyPanel({mode:"object",document:d,selectedId:"S0",selectedIds:["S0"],onSelect:vi.fn(),onChange:topology=>{changed=applyEditorCommand(d,{type:"set-physical-topology",topology});return true;}});
  expect(elements(tree).some(e=>e.props["aria-label"]==="Прозрачность пайпа")).toBe(false);
  const t={...d.physicalTopology!,joiningPipes:[{id:"op",start:{x:0,y:0},end:{x:100,y:0},path:{kind:"polyline" as const,points:[]},members:[{segmentIds:["S0"],from:.2,to:.8,reverse:false},{segmentIds:["S1"],from:.2,to:.8,reverse:false}],mode:"flat" as const}]};
  const opDoc={...d,physicalTopology:t};
  const opTree=DrawingObjectProperties({document:opDoc,objectId:"op",selectedIds:["op"],instances:[],onCommand:command=>{changed=applyEditorCommand(opDoc,command);return true;},onSelect:vi.fn()});
  expect(elements(opTree).some(e=>e.props["aria-label"]==="Прозрачность объединяющего пайпа")).toBe(false);
});
it("shows a volume mode toggle for a standalone pipe",()=>{
  const d=physicalFixture(),command=vi.fn<(command:EditorCommand)=>boolean>(()=>true);
  const markup=renderToStaticMarkup(createElement(DrawingObjectProperties,{document:d,objectId:"S0",selectedIds:["S0"],instances:[],onCommand:command,onSelect:vi.fn()}));
  expect(markup).toContain('aria-label="Объёмный режим пайпа"');
  const tree=PhysicalTopologyPanel({mode:"object",document:d,selectedId:"S0",selectedIds:["S0"],onSelect:vi.fn(),onChange:topology=>command({type:"set-physical-topology",topology})});
  const toggle=elements(tree).find(e=>e.props["aria-label"]==="Объёмный режим пайпа")!;
  expect(toggle.props.checked).toBe(false);
  toggle.props.onChange({target:{checked:true}});
  const change=command.mock.calls[0]![0];
  expect(change.type).toBe("set-physical-topology");
  if(change.type==="set-physical-topology")expect(change.topology.segments.find(s=>s.id==="S0")!.mode).toBe("round");
});
it("moves only the chosen coating behind the others",()=>{
  const d=physicalFixture(),cover=(id:string)=>({id,name:id,width:0,color:"#123456",lengthMm:null,spans:[{segmentId:"S0",from:0,to:1}]});
  const t={...d.physicalTopology!,coverings:[cover("a"),cover("b"),cover("c")]};let changed=t;
  const tree=PhysicalCoveringsPanel({compact:true,document:{...d,physicalTopology:t},topology:t,selectedIds:["b"],onReveal:vi.fn(),onChange:value=>{changed=value as typeof t;return true;}});
  elements(tree).find(e=>e.type==="button"&&e.props["aria-label"]==="На задний план")!.props.onClick();
  expect(changed.coverings.map(c=>c.id)).toEqual(["b","a","c"]);expect(changed.segments).toBe(t.segments);
});
it("keeps selected wires for manual routes and creates a sleeve for all selected pipes",()=>{
  const d=physicalFixture(),command=vi.fn<(command:EditorCommand)=>boolean>(()=>true);
  const wireTree=DrawingObjectProperties({document:d,objectId:"W1",selectedIds:["W1","W2"],instances:[],onCommand:command,onSelect:vi.fn()});
  expect(elements(wireTree).find(e=>e.type===PhysicalTopologyPanel)!.props.selectedIds).toEqual(["W1","W2"]);
  const pipeTree=DrawingObjectProperties({document:d,objectId:"S0",selectedIds:["S0","S2"],instances:[],onCommand:command,onSelect:vi.fn()});
  elements(pipeTree).find(e=>e.type==="button"&&e.props.children==="Оболочка на выделенные пайпы")!.props.onClick();
  const changed=applyEditorCommand(d,command.mock.calls[0]![0]);
  expect(changed.physicalTopology!.coverings![0]!.spans.map(s=>s.segmentId)).toEqual(["S0","S2"]);
});
it("shows the published wire database in selected wire properties",()=>{
  const d=physicalFixture(),binding={sourceId:"technology-wires",snapshotId:"11111111-1111-4111-8111-111111111111",
    snapshotSha256:"a".repeat(64),recordId:"b".repeat(64),entityType:"wire" as const,sourceKey:"МГТФ #1",displayName:"МГТФ · 0,35"};
  const markup=renderToStaticMarkup(createElement(DrawingObjectProperties,{document:d,objectId:"W1",selectedIds:["W1"],instances:[],onCommand:vi.fn(),onSelect:vi.fn(),
    wireMaterialOptions:[{id:binding.recordId,mark:"МГТФ",section:"0,35",label:binding.displayName,detail:"",materialBinding:binding}]}));
  expect(markup).toContain('aria-label="Марка материала провода"');
  expect(markup).toContain('value="МГТФ"');
  expect(markup).toContain('aria-label="Позиция материала провода"');
});
it("does not enable library drawing commands for a free connector",()=>{
  const tree=DrawingObjectProperties({document:physicalFixture(),objectId:"A",selectedIds:["A"],instances:[],onCommand:vi.fn(),onSelect:vi.fn()});
  expect(elements(tree).filter(e=>typeof e.props.onChange==="function").every(e=>e.props.disabled===true)).toBe(true);
  expect(elements(tree).some(e=>e.props["aria-label"]==="Вид со стороны контактов")).toBe(false);
});
it("renders the compact contact-side view card for a connector",()=>{
  const d=physicalFixture(),actions={onAdd:vi.fn(),onSelect:vi.fn(),onHide:vi.fn(),onScaleChange:vi.fn()};
  const card=ContactSideViewCard({state:{exists:false},actions});
  expect(card).toBeTruthy();expect(card.props.children).toBeTruthy();
  const add=elements(card).find(e=>e.type==="button"&&e.props.children==="Добавить вид со стороны контактов")!;
  add.props.onClick();expect(actions.onAdd).toHaveBeenCalledOnce();
});
it("exposes status, scale, select and hide actions for an existing contact-side view",()=>{
  const d=physicalFixture(),actions={onAdd:vi.fn(),onSelect:vi.fn(),onHide:vi.fn(),onScaleChange:vi.fn()};
  const card=ContactSideViewCard({state:{exists:true,visible:true,scale:1.25},actions});
  expect(elements(card).find(e=>e.props["data-status"]==="on-drawing")?.props.children).toBe("На чертеже");
  const scale=elements(card).find(e=>e.type===DrawingScaleControl)!;expect(scale.props.value).toBe(1.25);scale.props.onChange(2);expect(actions.onScaleChange).toHaveBeenCalledWith(2);
  elements(card).find(e=>e.type==="button"&&e.props.children==="Выделить")!.props.onClick();elements(card).find(e=>e.type==="button"&&e.props.children==="Скрыть")!.props.onClick();
  expect(actions.onSelect).toHaveBeenCalledOnce();expect(actions.onHide).toHaveBeenCalledOnce();
});
it("offers to show a hidden contact-side view without discarding its placement",()=>{
  const actions={onShow:vi.fn()};
  const card=ContactSideViewCard({state:{exists:true,visible:false,scale:1.25},actions});
  expect(elements(card).find(e=>e.props["data-status"]==="hidden")?.props.children).toBe("Скрыт");
  elements(card).find(e=>e.type==="button"&&e.props.children==="Показать")!.props.onClick();
  expect(actions.onShow).toHaveBeenCalledOnce();
});
it("keeps the popup inside viewport edges",()=>{
  expect(canvasPopoverPosition(1200,700,350,400,1280,720)).toEqual({left:922,top:312});
  expect(canvasPopoverPosition(-50,-20,350,400,1280,720)).toEqual({left:8,top:8});
});
it("waits more than one second before showing a hint and cancels a departed target",()=>{
  vi.useFakeTimers();const target={id:"S0",label:"S1",x:10,y:20};
  CanvasObjectHint({target});const cleanup=hooks.effects[0]!() as ()=>void;
  hooks.set.mockClear();vi.advanceTimersByTime(1000);expect(hooks.set).not.toHaveBeenCalled();
  vi.advanceTimersByTime(CANVAS_HINT_DELAY-1000);expect(hooks.set).toHaveBeenCalledExactlyOnceWith(target);
  cleanup();hooks.set.mockClear();const cancel=hooks.effects[0]!() as ()=>void;cancel();hooks.set.mockClear();
  vi.advanceTimersByTime(CANVAS_HINT_DELAY+1);expect(hooks.set).not.toHaveBeenCalled();
});

it("does not dismiss the property editor for a bubbled nested hint toggle",()=>{
 const close=vi.fn(),tree=CanvasObjectPopover({x:0,y:0,label:"Properties",children:null,onClose:close});
 expect(tree.props.popover).toBe("manual");
 const parent={},hint={};tree.props.onToggle({target:hint,currentTarget:parent,newState:"closed"});expect(close).not.toHaveBeenCalled();
 tree.props.onToggle({target:parent,currentTarget:parent,newState:"closed"});expect(close).toHaveBeenCalledOnce();
});
