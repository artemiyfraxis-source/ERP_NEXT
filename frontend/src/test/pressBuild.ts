import { fireEvent, screen, within } from "@testing-library/react";

/** «Создать» / «Пересобрать» генератора: если распиновка не проверена (pinoutCheck.ts), в вопросе — «Создать всё равно». */
export function pressBuild(name: "Создать" | "Пересобрать" = "Создать") {
  fireEvent.click(screen.getByRole("button", { name }));
  const ask = screen.queryByRole("dialog", { name: "Распиновка не проверена" });
  if (ask) fireEvent.click(within(ask).getByRole("button", { name: "Создать всё равно" }));
}
