/** Conservative text box inside the contour, including thick strokes and ellipses. */
export function contactShapeLabel(text:string,x:number,y:number,width:number,height:number,stroke:number,ellipse=false) {
  const inset=Math.max(Math.min(width,height)*.16,stroke/2+Math.min(width,height)*.08);
  const safeWidth=Math.max(0,width-2*inset)*(ellipse?Math.SQRT1_2:1);
  const safeHeight=Math.max(0,height-2*inset)*(ellipse?Math.SQRT1_2:1);
  // One em per character is deliberately wider than an Arial digit.
  const fontSize=Math.min(safeHeight*.72,safeWidth/Math.max(1,Array.from(text).length));
  return {text,x,y,fontSize,maxWidth:safeWidth};
}
export function contactLabelColor(fill:string|null) {
  if(!fill || !/^#[\da-f]{6}/i.test(fill))return "#172b3a";
  const [r,g,b]=[1,3,5].map(i=>parseInt(fill.slice(i,i+2),16));
  return .299*r!+.587*g!+.114*b!<145?"#ffffff":"#172b3a";
}
