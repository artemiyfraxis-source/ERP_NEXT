import { useEffect,useLayoutEffect,useRef,useState } from "react";
import { canvasPopoverPosition } from "./CanvasObjectPopover";

export interface CanvasHintTarget {id:string;label:string;x:number;y:number}
export const CANVAS_HINT_DELAY=1100;
export function CanvasObjectHint({target}:{target:CanvasHintTarget|null}) {
  const [visible,setVisible]=useState<CanvasHintTarget|null>(null),ref=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    setVisible(null);if(!target)return;
    const timer=setTimeout(()=>setVisible(target),CANVAS_HINT_DELAY);
    return ()=>clearTimeout(timer);
  },[target]);
  useLayoutEffect(()=>{
    const element=ref.current;if(!visible||!element)return;
    element.showPopover();
    const box=element.getBoundingClientRect(),p=canvasPopoverPosition(visible.x+12,visible.y+16,box.width,box.height,innerWidth,innerHeight);
    element.style.left=`${p.left}px`;element.style.top=`${p.top}px`;
  },[visible]);
  return visible?<div ref={ref} popover="manual" role="tooltip" className="he-object-hint"><strong>{visible.label}</strong><span>Правая кнопка — свойства</span></div>:null;
}
