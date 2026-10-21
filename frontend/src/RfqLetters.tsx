import { useState } from "react";
import { failureText } from "./api";
import { formatDay, formatNumber, unitLabel } from "./format";
import { rfqApi, type RfqLine, type RfqMatrix, type RfqSupplier } from "./purchasingRfqApi";

/** «Письма поставщикам» (Roller_Buyer RfqLetters): the request as a letter the buyer copies into the mail — the ERP
 *  sends no mail itself. Each supplier gets the lines asked of him only (suppliers asked for the same lines share one
 *  letter). The calculation's planned price is never in it: the supplier must not see the price we would settle for.
 *  No signature either — the buyer's mail adds its own. Using a supplier's letter (copying it for him, his address, or
 *  «Открыть в почте») marks the request «отправлено» to him. */

// Plain spaces between thousands (\s takes the thin and the no-break space): a letter goes into any mail program.
export const qty = (value: number | string) => formatNumber(value, { digits: 3 }).replace(/\s/g, " ");

/** Quantities the letter asks for: the request's by default, more when the buyer wants a better price. */
export type AskQty = Record<string, number>;

/** «1 500,5», «2,5» → a number; empty or garbage → NaN. */
export function qtyNum(text: string): number {
  const value = Number(text.replace(/\s/g, "").replace(",", "."));
  return text.trim() === "" || !Number.isFinite(value) ? Number.NaN : value;
}

/** «500; 1000», «500, 1000», «500 1000» → [500, 1000]; garbage is dropped. */
export function qtyList(text: string): number[] {
  return text.split(/[;\s]+|,(?!\d)/).map(qtyNum).filter((value) => value > 0);
}

function lineText(line: RfqLine, index: number, ask: AskQty): string {
  const name = line.article && !line.name.includes(line.article) ? `${line.name} (${line.article})` : line.name;
  const maker = line.manufacturer && !name.toLowerCase().includes(line.manufacturer.toLowerCase()) ? `, ${line.manufacturer}` : "";
  const need = line.need_by ? ` — нужно к ${formatDay(line.need_by)}` : "";
  return `${index + 1}. ${name}${maker} — ${qty(ask[line.id] ?? line.quantity)} ${unitLabel(line.unit)}${need}`;
}

const positions = (count: number) => (count % 10 === 1 && count % 100 !== 11 ? "позиция"
  : count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14) ? "позиции" : "позиций");

export function letterSubject(matrix: RfqMatrix, lines: RfqLine[]): string {
  return `Запрос КП № ${matrix.rfq.number} от ${formatDay(matrix.rfq.created_at)} — ${lines.length} ${positions(lines.length)}`;
}

export function letterBody(lines: RfqLine[], answerBy: string | null, ask: AskQty = {}, extra: number[] = []): string {
  return [
    "Добрый день!",
    "",
    "Просим направить коммерческое предложение на следующие позиции:",
    "",
    ...lines.map((line, index) => lineText(line, index, ask)),
    "",
    "В предложении, пожалуйста, укажите:",
    "— цену за единицу с НДС (если цена без НДС — отметьте это);",
    "— срок поставки или наличие на складе;",
    "— минимальную партию и кратность упаковки, если есть;",
    "— если цена зависит от количества — ступени цены (от какого количества какая цена);",
    "— срок действия предложения.",
    ...(extra.length ? ["", `Также просим указать цену при заказе ${extra.map((value) => `от ${qty(value)}`).join(" и ")} (в тех же единицах) по каждой позиции.`] : []),
    "",
    "Если какую-то позицию поставить не можете — отметьте это в ответе. Аналоги предлагайте, только если оригинала нет, с указанием производителя.",
    ...(answerBy ? ["", `Ответ просим направить до ${formatDay(answerBy)}.`] : []),
    "",
    "Заранее спасибо!",
  ].join("\n");
}

/** For a supplier bought from on a website: what to put in the cart. */
export function cartList(lines: RfqLine[], ask: AskQty = {}): string {
  return lines.map((line) => `${line.article || line.name}\t${qty(ask[line.id] ?? line.quantity)} ${unitLabel(line.unit)}`).join("\n");
}

/** «2026-10-01» + 3 → «2026-10-04». */
export function addDays(day: string, days: number): string {
  const moment = new Date(`${day.slice(0, 10)}T00:00:00Z`);
  moment.setUTCDate(moment.getUTCDate() + Math.round(days));
  return moment.toISOString().slice(0, 10);
}

function localToday(): string {
  const now = new Date();
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** The lines a supplier is asked for (not cancelled). */
export function supplierLines(matrix: RfqMatrix, supplier: RfqSupplier): RfqLine[] {
  return matrix.lines.filter((line) => !line.cancelled && (!supplier.line_ids || supplier.line_ids.includes(line.id)));
}

export type LetterGroup = { key: string; lines: RfqLine[]; suppliers: RfqSupplier[] };

/** One letter per set of lines: suppliers asked for the same lines get the same text. A supplier who refused to
 *  answer is not written to again. */
export function letterGroups(matrix: RfqMatrix): LetterGroup[] {
  const groups = new Map<string, LetterGroup>();
  for (const supplier of matrix.suppliers) {
    if (supplier.declined_at) continue;
    const lines = supplierLines(matrix, supplier);
    if (lines.length === 0) continue;
    const key = lines.map((line) => line.id).join(",");
    const group = groups.get(key) ?? { key, lines, suppliers: [] };
    group.suppliers.push(supplier);
    groups.set(key, group);
  }
  return [...groups.values()];
}

/** Answer by: the day the first of them was sent the request (else today) + «Ждать ответа на запрос». */
export function answerBy(suppliers: RfqSupplier[], waitDays: number | undefined, today = localToday()): string | null {
  if (waitDays === undefined) return null;
  const sent = suppliers.map((supplier) => supplier.sent_at?.slice(0, 10)).filter((day): day is string => Boolean(day)).sort()[0];
  return addDays(sent ?? today, waitDays);
}

export function mailtoHref(email: string, subject: string, body: string): string {
  return `mailto:${email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

/** Into the clipboard; outside a secure context (the ERP opened by a network address) — the old way still works. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    let done = false;
    try { done = document.execCommand("copy"); } catch { done = false; }
    area.remove();
    return done;
  }
}

/** «Скопировано ✓» on the button just used, for two seconds. */
export function useCopied() {
  const [copied, setCopied] = useState<string | null>(null);
  async function copy(key: string, text: string): Promise<boolean> {
    const done = await copyText(text);
    if (done) {
      setCopied(key);
      window.setTimeout(() => setCopied((current) => (current === key ? null : current)), 2000);
    }
    return done;
  }
  const label = (key: string, text: string) => (copied === key ? "Скопировано ✓" : text);
  return { copy, label };
}

export function RfqLetters({ matrix, editable, onClose, onSent }: {
  matrix: RfqMatrix; editable: boolean; onClose: () => void; onSent: (message: string) => void;
}) {
  const { copy, label } = useCopied();
  const [askText, setAskText] = useState<Record<string, string>>({});
  const [extraText, setExtraText] = useState("");
  const [error, setError] = useState("");
  const ask: AskQty = {};
  for (const [id, text] of Object.entries(askText)) {
    const value = qtyNum(text);
    if (value > 0) ask[id] = value;
  }
  const extra = qtyList(extraText);
  const groups = letterGroups(matrix);
  const lines = matrix.lines.filter((line) => !line.cancelled);

  /** The letter went to him: «Отправлено» on the request (once; only the request's buyer marks it). */
  async function used(suppliers: RfqSupplier[]) {
    const fresh = editable ? suppliers.filter((supplier) => !supplier.sent_at) : [];
    if (fresh.length === 0) return;
    setError("");
    try {
      for (const supplier of fresh) await rfqApi.markSent(matrix.rfq.id, supplier.supplier_id);
      onSent(`Отмечено отправленным: ${fresh.map((supplier) => supplier.name).join(", ")}`);
    } catch (reason) {
      setError(failureText(reason, "Отправка не отмечена"));
    }
  }

  return (
    <section className="panel rfq-letters" aria-label="Письма поставщикам">
      <div className="rfq-letters-head">
        <h3 title="ERP сама почту не отправляет: скопируйте текст в письмо. Подпись добавит ваша почта. Плановую цену из расчёта в письмо не ставим — поставщик не должен знать, на что мы согласны.">
          Письма поставщикам
        </h3>
        <span className="muted">Почту ERP не отправляет: скопируйте текст или откройте в почте. Плановой цены в письме нет.</span>
        <button type="button" className="secondary small rfq-letters-close" onClick={onClose}>Свернуть</button>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <details className="rfq-letter-qty">
        <summary>Количества в письме — запросить больше или цену на крупную партию</summary>
        <label className="rfq-letter-extra">Также запросить цену при заказе от (через запятую)
          <input placeholder="500, 1000" value={extraText} onChange={(event) => setExtraText(event.target.value)} />
        </label>
        <table className="grid">
          <thead><tr><th className="grow">Позиция</th><th className="num">По заявке</th><th className="num">В письме</th></tr></thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.id}>
                <td className="text">{line.name}</td>
                <td className="num">{qty(line.quantity)} {unitLabel(line.unit)}</td>
                <td className="num">
                  <input
                    className="rfq-letter-ask" aria-label={`В письме ${line.name}`} placeholder={qty(line.quantity)}
                    value={askText[line.id] ?? ""} onChange={(event) => setAskText((current) => ({ ...current, [line.id]: event.target.value }))}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted">Это меняет только текст письма. Потребность проекта остаётся из заявки: излишек при покупке уйдёт в свободный остаток.</p>
      </details>
      {groups.length === 0 && <p className="muted">Писать некому: в запросе нет поставщиков (или все отказались).</p>}
      {groups.map((group) => {
        const subject = letterSubject(matrix, group.lines);
        const body = letterBody(group.lines, answerBy(group.suppliers, matrix.settings.quote_wait_days), ask, extra);
        const key = (what: string) => `${group.key}-${what}`;
        const single = group.suppliers.length === 1 ? group.suppliers : [];
        return (
          <section key={group.key} className="rfq-letter" aria-label={`Письмо: ${group.suppliers.map((supplier) => supplier.name).join(", ")}`}>
            {groups.length > 1 && (
              <div className="rfq-letter-to-line">
                <strong>{group.suppliers.map((supplier) => supplier.name).join(", ")}</strong>
                <span className="muted"> · {group.lines.length} {positions(group.lines.length)} из {lines.length}</span>
              </div>
            )}
            <div className="rfq-letter-subject"><span className="muted">Тема:</span> {subject}</div>
            <pre className="rfq-letter-body">{body}</pre>
            <div className="rfq-letter-actions">
              <button type="button" className="primary small" onClick={() => void copy(key("body"), body).then((done) => { if (done) void used(single); })}>
                {label(key("body"), "Скопировать текст")}
              </button>
              <button type="button" className="secondary small" onClick={() => void copy(key("subject"), subject)}>{label(key("subject"), "Скопировать тему")}</button>
              <button type="button" className="secondary small" onClick={() => void copy(key("cart"), cartList(group.lines, ask))}>
                {label(key("cart"), "Список для корзины на сайте")}
              </button>
            </div>
            <table className="grid rfq-letter-to">
              <thead><tr><th>Кому</th><th className="grow">Почта</th><th>Отправлено</th><th aria-label="Действия" /></tr></thead>
              <tbody>
                {group.suppliers.map((supplier) => (
                  <tr key={supplier.supplier_id}>
                    <td>{supplier.name}{supplier.contact_person ? <small className="muted"> · {supplier.contact_person}</small> : null}</td>
                    <td>
                      {supplier.email
                        ? <span className="mono">{supplier.email}</span>
                        : <span className="muted">нет в карточке поставщика — впишите почту в «Поставщики»{supplier.site ? " или отправьте с сайта" : ""}</span>}
                    </td>
                    <td>
                      {supplier.sent_at ? formatDay(supplier.sent_at) : editable
                        ? <button type="button" className="link-button" onClick={() => void used([supplier])}>отметить</button>
                        : "—"}
                    </td>
                    <td className="rfq-letter-to-actions">
                      {supplier.email && <>
                        <button type="button" className="secondary small" onClick={() => void copy(key(`to-${supplier.supplier_id}`), supplier.email!).then((done) => { if (done) void used([supplier]); })}>
                          {label(key(`to-${supplier.supplier_id}`), "Скопировать адрес")}
                        </button>
                        <a className="secondary small" href={mailtoHref(supplier.email, subject, body)} onClick={() => void used([supplier])}>Открыть в почте</a>
                      </>}
                      {!supplier.email && supplier.site && <a className="secondary small" href={supplier.site} target="_blank" rel="noreferrer">Сайт ↗</a>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        );
      })}
    </section>
  );
}
