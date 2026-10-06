// @vitest-environment node
import { expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import { DrawingRangeControl } from "./DrawingRangeControl";

vi.mock("react",async original=>({...await original<typeof import("react")>(),useId:()=>"range-test",useRef:(current:unknown)=>({current})}));
function fixture(){
  const preview=vi.fn(),commit=vi.fn();
  const tree=DrawingRangeControl({label:"Позиции",accessibleLabel:"Scale",value:1,min:.25,max:4,step:.05,hint:"Hint",onPreview:preview,onCommit:commit});
  const [label,,,input]=tree.props.children as ReactElement<Record<string,unknown>>[];
  expect(label!.props.htmlFor).toBe(input!.props.id);
  return {preview,commit,handlers:input!.props as Record<string,(e?:unknown)=>void>};
}
it.each(["onPointerUp","onKeyUp","onBlur"])("previews immediately and commits once on %s",event=>{
  const {handlers:h,preview,commit}=fixture();
  h.onChange!({target:{value:"1.5"}});h.onChange!({target:{value:"2"}});
  expect(preview).toHaveBeenLastCalledWith(2);expect(commit).not.toHaveBeenCalled();
  h[event]!();h.onBlur!();h.onLostPointerCapture!();
  expect(commit).toHaveBeenCalledExactlyOnceWith(2);expect(preview).toHaveBeenLastCalledWith(null);
});
it.each(["Escape","onPointerCancel","onLostPointerCapture"])("cancels preview on %s",event=>{
  const {handlers:h,preview,commit}=fixture();
  h.onChange!({target:{value:"3"}});
  if(event==="Escape")h.onKeyDown!({key:"Escape",stopPropagation:vi.fn(),preventDefault:vi.fn()});else h[event]!();
  h.onKeyUp!();h.onPointerUp!();h.onBlur!();
  expect(commit).not.toHaveBeenCalled();expect(preview).toHaveBeenLastCalledWith(null);
});
