export const drawingToolLabels = {line:"Линия",polyline:"Ломаная",rectangle:"Прямоугольник",ellipse:"Эллипс",bezier:"Безье",closedContour:"Контур",text:"Текст",contact:"Контакт"};
export function DrawingToolIcon({kind}:{kind:keyof typeof drawingToolLabels}) {
  const paths={line:"M4 20L20 4",polyline:"M3 19L8 6L15 17L21 4",rectangle:"M4 5H20V19H4Z",ellipse:"M20 12A8 6 0 1 0 4 12A8 6 0 1 0 20 12",bezier:"M3 18C5 0 19 24 21 6",closedContour:"M4 18L6 5L17 3L21 15L12 21Z",text:"M4 5H20M12 5V20M8 20H16",contact:"M4 12H10M20 12A5 5 0 1 0 10 12A5 5 0 1 0 20 12"};
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[kind]}/></svg>;
}
