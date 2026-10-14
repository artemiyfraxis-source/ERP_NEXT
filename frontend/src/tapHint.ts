/* Подсказки `title` на телефоне и планшете (владелец 13.10.2026): наведения пальцем нет, поэтому подсказка
   «откуда взято имя», «цифры этапа» не открывалась вовсе. Один обработчик на весь документ: нажатие на элемент
   с `title` показывает его текст рядом. Кнопки, ссылки, поля и строки с `title` не трогаем — их нажатие
   выполняет своё действие, обработчик ничего не отменяет и не останавливает. Работает только на сенсорном
   экране (pointer: coarse). */

const INTERACTIVE = "button, a, input, select, textarea, label, summary, [role='button'], [role='tab'], [role='menuitem']";
const SHOW_MS = 5000;

export function installTapHint(doc: Document = document): () => void {
  const win = doc.defaultView;
  if (!win || typeof win.matchMedia !== "function" || !win.matchMedia("(pointer: coarse)").matches) return () => undefined;

  let hint: HTMLElement | null = null;
  let timer: number | undefined;

  const hide = () => {
    if (timer !== undefined) win.clearTimeout(timer);
    timer = undefined;
    hint?.remove();
    hint = null;
  };

  const show = (owner: HTMLElement, text: string) => {
    hide();
    const box = doc.createElement("div");
    box.className = "tap-hint";
    box.setAttribute("role", "tooltip");
    box.textContent = text;
    doc.body.appendChild(box);
    const at = owner.getBoundingClientRect();
    const width = Math.min(box.offsetWidth, win.innerWidth - 16);
    const left = Math.max(8, Math.min(at.left, win.innerWidth - width - 8));
    const below = at.bottom + 6 + box.offsetHeight <= win.innerHeight;
    box.style.left = `${left}px`;
    box.style.top = `${below ? at.bottom + 6 : Math.max(8, at.top - 6 - box.offsetHeight)}px`;
    hint = box;
    timer = win.setTimeout(hide, SHOW_MS);
  };

  const onClick = (event: Event) => {
    const target = event.target;
    if (!(target instanceof win.Element)) return;
    if (target.closest(".tap-hint")) return;
    // Нажатие по кнопке, ссылке или полю внутри подписанного блока — их собственное дело.
    if (target.closest(INTERACTIVE)) return hide();
    const owner = target.closest<HTMLElement>("[title]");
    const text = owner?.getAttribute("title")?.trim();
    if (!owner || !text || owner.tagName === "TR") return hide();
    if (hint && hint.textContent === text) return hide();
    show(owner, text);
  };

  doc.addEventListener("click", onClick);
  doc.addEventListener("scroll", hide, true);
  return () => {
    doc.removeEventListener("click", onClick);
    doc.removeEventListener("scroll", hide, true);
    hide();
  };
}
