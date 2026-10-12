import type { FulfillmentCandidate } from "./api";

/* «Возможно есть» (owner 12.10.2026): the row of a position is coloured by what the warehouse may hold for it.
   done — nothing left to pick · ok — an exact article with enough free quantity · maybe — only lookalikes, or exact but
   too little · none — nothing found. The articles on the warehouse are not normalized yet, hence «maybe».
   Shared by «Запросы на комплектацию» and the project's «Снабжение»; the matcher itself is the server's
   (`app.shared.article_match`). */
export type CandidateTone = "done" | "ok" | "maybe" | "none";

export function candidateTone(remaining: number, candidates: FulfillmentCandidate[]): CandidateTone {
  if (remaining <= 0.0001) return "done";
  if (!candidates.length) return "none";
  const exact = candidates.filter((candidate) => candidate.kind === "exact");
  return exact.reduce((sum, candidate) => sum + Number(candidate.available), 0) >= remaining - 0.0001 ? "ok" : "maybe";
}

export const CANDIDATE_LEGEND: Array<{ tone: CandidateTone; text: string }> = [
  { tone: "ok", text: "есть, хватает" },
  { tone: "maybe", text: "возможно есть: похожий артикул или не хватает" },
  { tone: "none", text: "на складе не найдено" },
  { tone: "done", text: "уже набрано" },
];

export function matchText(candidate: FulfillmentCandidate) {
  return candidate.kind === "exact" ? "тот же артикул" : "похожий артикул";
}
