// @vitest-environment node
import { expect, it, vi } from "vitest";
import { DrawingResizeGrip } from "./DrawingResizeGrip";

vi.mock("react", () => ({
  useRef: <T,>(current: T) => ({current}),
}));

it("previews and commits a proportional scale from a pointer drag",()=>{
  const preview=vi.fn(),commit=vi.fn();
  const grip=DrawingResizeGrip({x:100,y:100,vx:100,vy:100,scale:1,preview,commit});
  const target={setPointerCapture:vi.fn(),releasePointerCapture:vi.fn()};
  const start={button:0,pointerId:3,clientX:20,clientY:30,currentTarget:target,preventDefault:vi.fn(),stopPropagation:vi.fn()};
  grip.props.onPointerDown(start);
  grip.props.onPointerMove({...start,clientX:120});
  expect(preview).toHaveBeenLastCalledWith(1.5);
  grip.props.onPointerUp({...start,clientX:120});
  expect(preview).toHaveBeenLastCalledWith(null);
  expect(commit).toHaveBeenCalledExactlyOnceWith(1.5);
  expect(target.releasePointerCapture).toHaveBeenCalledWith(3);
});

it("does not commit a cancelled scale gesture",()=>{
  const preview=vi.fn(),commit=vi.fn();
  const grip=DrawingResizeGrip({x:100,y:100,vx:100,vy:100,scale:1,preview,commit});
  const target={setPointerCapture:vi.fn(),releasePointerCapture:vi.fn()};
  const start={button:0,pointerId:3,clientX:20,clientY:30,currentTarget:target,preventDefault:vi.fn(),stopPropagation:vi.fn()};
  grip.props.onPointerDown(start);
  grip.props.onPointerMove({...start,clientX:120});
  grip.props.onPointerCancel();
  grip.props.onPointerUp({...start,clientX:120});
  expect(preview).toHaveBeenLastCalledWith(null);
  expect(commit).not.toHaveBeenCalled();
});
