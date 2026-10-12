import { useStageOption } from "./StageOptions";

/* Stored purposes stay as they are (GRN prefixes depend on them); only the screen shows full words. */
const LABELS: Record<string, string> = {
  RND: "НИОКР (опытный образец)",
  FRX: "Собственные нужды компании",
  "Хоз деятельность": "Хозяйственная деятельность",
  "Коммерческий проект": "Коммерческий проект (заказ клиента)",
};

/** Full words for a stored purpose; a text that is not a purpose (a project number) comes back as written.
 *  «RND · 48К.13» is read part by part. */
export function purposeLabel(value: string | null | undefined): string {
  const text = String(value ?? "");
  return text.split(" · ").map((part) => LABELS[part.trim()] ?? part).join(" · ");
}

/** Label function that follows the stage option `requests.form`: off = the value as stored. */
export function usePurposeLabel(): (value: string | null | undefined) => string {
  const on = useStageOption("requests.form");
  return on ? purposeLabel : (value) => String(value ?? "");
}
