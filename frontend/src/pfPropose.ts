/* «Предложить разбивку на ПФ» (владелец 13.10): правило разбивки — на сервере (techcards/domain/pf_split.py, одно на
   кнопку, сборку генератором и перенос старых ЭТК); здесь — вызов и перевод ответа в правимую схему редактора ПФ.
   Ничего не сохраняет: предложение ложится в редактор, технолог его правит и сохраняет кнопкой «Сохранить ПФ». */

import { techcardsApi, type ProjectTechcard } from "./api";
import { etkRouteOf } from "./etkRoute";
import type { Route } from "./routeLogic";

export type PfProposal = { route: Route; same: boolean };

/** Предложение сервера для ЭТК: схема для редактора и «уже так разбита». */
export async function proposePf(cardId: string): Promise<PfProposal> {
  const answer = await techcardsApi.proposeRoute(cardId);
  const parsed = etkRouteOf({ route: answer.route });
  const route: Route = parsed
    ? { blocks: parsed.blocks.map(({ key, name, ops, final, components }) => ({ key, name, ops, final, components })), edges: parsed.edges.map((edge) => ({ ...edge })) }
    : { blocks: [], edges: [] };
  return { route, same: answer.same };
}

/** ЭТК со схемой «по ПФ на операцию» (до разбивки на ПФ, `derived`) — технологу её надо пройти. Несобранная — нет. */
export function pfPending(card: Pick<ProjectTechcard, "document">): boolean {
  const route = etkRouteOf(card.document);
  return Boolean(route) && route!.source === "derived";
}
