// End treatments of a wire blank on the drawing. The editable blank catalogue of TECHMAP-GRAPHER
// (photos, published reference table, route illustrations) is not part of the ERP port.
export const wireBlankEnds = ["cut", "copper", "tin", "terminal", "sealed", "sealed-pin"] as const;
export type WireBlankEnd = typeof wireBlankEnds[number];
export const wireBlankEndLabels: Readonly<Record<WireBlankEnd, string>> = {
  cut: "После резки", copper: "Зачищен · медь", tin: "Лужён",
  terminal: "Наконечник", sealed: "Наконечник с уплотнителем",
  "sealed-pin": "Штыревой наконечник с уплотнителем",
};
