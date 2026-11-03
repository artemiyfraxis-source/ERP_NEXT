/** Stable project identity color shared by plan rows and project cards (`--zg-c0…11`). */
export function projectColor(project: string) {
  let hash = 0;
  for (const char of project) hash = (hash * 31 + (char.codePointAt(0) ?? 0)) >>> 0;
  return hash % 12;
}
