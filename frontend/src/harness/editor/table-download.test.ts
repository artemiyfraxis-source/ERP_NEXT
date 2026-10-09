import { describe, expect, it } from "vitest";
import { cellText, tableFileName, tablesText, utf16WithBom } from "./table-download";

function root(html: string) {
  const element = document.createElement("div");
  element.innerHTML = html;
  return element;
}

describe("«Скачать» окна таблицы чертежа", () => {
  it("берёт таблицы как на экране: значения полей и выбранный пункт, без кнопок порядка и значков", () => {
    const element = root(`
      <table><thead><tr><th>Поз.</th><th>Индекс</th><th>Сечение</th><th>Примечание</th></tr></thead>
      <tbody><tr>
        <td><button>1</button><button class="he-position-toggle" aria-label="Скрыть"><svg><path/></svg></button></td>
        <td><input value="XP1"></td>
        <td><select><option value="a">0,5</option><option value="b" selected>0,75</option></select></td>
        <td><input value="Без   привязки"><div class="he-relations-actions"><button>↑</button></div></td>
      </tr></tbody></table>
      <table><tr><td><button>W1 ↗</button></td><td>100 мм</td></tr></table>`);
    expect(tablesText(element)).toBe(
      "Поз.\tИндекс\tСечение\tПримечание\r\n1\tXP1\t0,75\tБез привязки\r\n\r\nW1\t100 мм\r\n",
    );
    expect(cellText(root("<span> a\n b </span>"))).toBe("a b");
  });

  it("файл: имя чертежа и таблицы, UTF-16 с BOM для Excel", () => {
    expect(tableFileName("КБ1 Жгут питания", "Таблица соединений")).toBe("КБ1 Жгут питания — Таблица соединений.xls");
    expect(tableFileName("", "Спецификация")).toBe("Спецификация.xls");
    expect(tableFileName("708К.4/КБ1", "Резка и разделка")).toBe("708К.4_КБ1 — Резка и разделка.xls");
    expect(Array.from(utf16WithBom("Я"))).toEqual([0xff, 0xfe, 0x2f, 0x04]);
  });
});
