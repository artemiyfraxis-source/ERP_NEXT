import { useLayoutEffect, useRef, type ReactNode } from "react";

export function canvasPopoverPosition(x:number,y:number,width:number,height:number,viewportWidth:number,viewportHeight:number) {
  return {left:Math.max(8,Math.min(x,viewportWidth-width-8)),top:Math.max(8,Math.min(y,viewportHeight-height-8))};
}

/** Native top layer: property controls and their InfoHints escape canvas clipping. */
export function CanvasObjectPopover({x,y,label,children,onClose}: {
  x:number;y:number;label:string;children:ReactNode;onClose:()=>void;
}) {
  const ref=useRef<HTMLDivElement>(null),close=useRef(onClose);close.current=onClose;
  useLayoutEffect(()=>{
    const element=ref.current;if(!element)return;
    element.showPopover();
    element.focus({preventScroll:true});
    const position=()=>{
      const size=element.getBoundingClientRect(),p=canvasPopoverPosition(x,y,size.width,size.height,innerWidth,innerHeight);
      element.style.left=`${p.left}px`;element.style.top=`${p.top}px`;
    };
    position();
    const resize=new ResizeObserver(position);resize.observe(element);window.addEventListener("resize",position);
    return ()=>{resize.disconnect();window.removeEventListener("resize",position);};
  },[x,y]);
  // Keep the properties surface independent from nested InfoHint popovers. The
  // automatic popover mode closes when another top-layer surface opens, which
  // made a hint click discard the whole right-click editor.
  return <div ref={ref} popover="manual" role="dialog" tabIndex={-1} aria-label={label} className="he-object-popover"
    onToggle={e=>{if(e.target===e.currentTarget&&e.newState==="closed")close.current();}}
    onKeyDown={e=>{if(e.key==="Escape"){e.stopPropagation();onClose();}}}>
    <header className="ui-section-heading"><strong>{label}</strong><button type="button" className="ui-control" aria-label="Закрыть свойства" onClick={onClose}>×</button></header>
    {children}
  </div>;
}
