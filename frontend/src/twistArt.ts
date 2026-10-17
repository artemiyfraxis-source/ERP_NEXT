// Свитая пара одним способом для эскиза ПФ и схемы связей (владелец 17.10: «свивку сделать нормальной»): два провода в своих
// цветах пересекаются с постоянным шагом — плавная двойная спираль, на пересечении один провод поверх другого.
// Провод i из n сдвинут по фазе на 2π·i/n; «спереди» он там, где косинус его фазы положителен — эти участки рисуются
// поверх остальных. Шаг — расстояние по оси X на один полный оборот.
export type TwistPoint = [number, number];
export type TwistWire = { points: TwistPoint[]; front: TwistPoint[][] };

/** Точки провода `index` из `count` на отрезке [x0, x1] вокруг оси `axis(x)`: шаг `pitch`, размах `amp`, и участки «спереди». */
export function twistWire(x0: number, x1: number, axis: (x: number) => number, pitch: number, amp: number, index: number,
  count = 2, step = 2): TwistWire {
  const phase = (2 * Math.PI * index) / Math.max(2, count), points: TwistPoint[] = [], front: TwistPoint[][] = [];
  let run: TwistPoint[] = [];
  for (let x = x0; ; x = Math.min(x1, x + step)) {
    const angle = (2 * Math.PI * (x - x0)) / pitch + phase;
    const point: TwistPoint = [x, axis(x) + amp * Math.sin(angle)];
    points.push(point);
    if (Math.cos(angle) > 0) run.push(point);
    else if (run.length) { front.push(run); run = []; }
    if (x >= x1) break;
  }
  if (run.length) front.push(run);
  return { points, front };
}

/** Точки в путь SVG: «M x,y L x,y …». */
export function pointsPath(points: TwistPoint[]): string {
  return points.map(([x, y], k) => `${k ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
}

/** Свитая пара на прямой оси `y` от x0 до x1: оба провода целиком, затем поверх них участки «спереди» — со своей
 *  обводкой, чтобы на пересечении было видно, кто сверху. `colors` — цвета проводов; `width` — толщина провода. */
export function twistedPairSvg(x0: number, x1: number, y: number, pitch: number, amp: number, colors: [string, string],
  width = 4.4, ink = "#17272b"): string {
  const wires = [0, 1].map((index) => twistWire(x0, x1, () => y, pitch, amp, index));
  const stroke = (d: string, color: string, part: boolean) => `<path${part ? ' data-part="twist"' : ""} d="${d}" fill="none" stroke="${ink}" stroke-width="${(width + 2).toFixed(1)}" stroke-linecap="butt" stroke-linejoin="round"/>` +
    `<path d="${d}" fill="none" stroke="${color}" stroke-width="${width}" stroke-linecap="butt" stroke-linejoin="round"/>`;
  return wires.map((wire, k) => stroke(pointsPath(wire.points), colors[k], k === 0)).join("") +
    wires.map((wire, k) => wire.front.map((run) => stroke(pointsPath(run), colors[k], false)).join("")).join("");
}
