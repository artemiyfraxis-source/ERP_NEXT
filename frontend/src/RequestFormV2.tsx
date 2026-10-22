import { useEffect, useMemo, useRef, useState } from "react";
import { useFormDraft } from "./formDrafts";
import { requestsApi, type RequestSchema } from "./api";
import { ComboBox, type ComboOption } from "./ComboBox";
import { purposeLabel } from "./purposeLabels";
import type { ItemRow } from "./purchasingItemsApi";
import { RequestGridV2 } from "./RequestGridV2";
import { openSection, urlParam } from "./navigation";
import {
  GRID_LIMIT, applyPaste, isBlankRow, positionsWord, type Draft, type GridKey,
} from "./requestGridLogic";
import {
  CREATE_INTERNAL_KEY, applyHeaderToAll, chooseProject, customerKey, emptyHeader, emptyLine,
  hasErrors, internalProjectKey, payloadV2, pointLabel, projectKeyFor, validateForm, type Header,
} from "./requestFormV2Logic";

type Props = {
  schema: RequestSchema | null;
  /** «Вставить из Excel» hands its text over; every new `id` is applied once */
  seed?: { id: number; text: string } | null;
  onCreated: (ids: string[]) => void | Promise<void>;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
};

/** Unsent form kept in the shell's memory (F07): survives a session expiry, another section and a sign-out/sign-in of the same user. */
interface FormV2Draft { creationKey: string; header: Header; lines: Draft[] }

/** «Оформить заявку»: nothing pre-filled, project required, header values are defaults and a line value overrides them. */
export function RequestFormV2({ schema, seed, onCreated, onError, onNotice }: Props) {
  const draft = useFormDraft<FormV2Draft>("requests:form-v2");
  const restored = draft.initial;
  const creationKey = useRef(restored?.creationKey ?? crypto.randomUUID());
  const [header, setHeader] = useState<Header>(() => restored?.header ?? emptyHeader());
  const [lines, setLines] = useState<Draft[]>(() => (restored?.lines.length ? restored.lines : [emptyLine()]));
  const [showErrors, setShowErrors] = useState(false);
  const [busy, setBusy] = useState(false);
  const lastSeed = useRef(0);

  const purposes = schema?.purposes ?? [];
  const units = schema?.units ?? [];

  const internal = schema?.internal_projects ?? [];

  const projectOptions: ComboOption[] = useMemo(() => [
    // The registry also lists the open «ВП-…» numbers: they belong to the group below, not to the customers'.
    ...(schema?.projects ?? []).filter((number) => !/^ВП-\d+$/.test(number))
      .map((number) => ({ value: customerKey(number), label: number, group: "Проекты заказчиков" })),
    ...(internal.length
      ? internal.map((item) => ({ value: internalProjectKey(item.number), label: `${item.number} · ${item.name}`, group: "Внутренние проекты" }))
      : [{ value: CREATE_INTERNAL_KEY, label: "Создать внутренний проект…", group: "Внутренние проекты" }]),
  ], [schema, internal]);

  // «Новая заявка» from «Заявки» закупщика comes with `?project=`: the project is chosen once the lists are in.
  const prefilled = useRef(false);
  useEffect(() => {
    if (prefilled.current || !schema) return;
    prefilled.current = true;
    const key = projectKeyFor(urlParam("project"), schema.projects ?? [], purposes, internal, true);
    if (key) setHeader((current) => (current.project ? current : chooseProject(current, key, internal)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schema]);

  function pickProject(key: string) {
    if (key === CREATE_INTERNAL_KEY) { openSection("projects", { tab: "internal" }); return; }
    setHeader((current) => chooseProject(current, key, internal));
  }

  const errors = useMemo(() => validateForm(lines, header, units), [lines, header, units]);
  const sendRows = lines.filter((line) => !isBlankRow(line));

  // The key stays with the draft: a repeat after a lost answer is the same creation, never a duplicate.
  useEffect(() => {
    const untouched = !sendRows.length && JSON.stringify(header) === JSON.stringify(emptyHeader());
    draft.save(untouched ? null : { creationKey: creationKey.current, header, lines });
  }, [draft, header, lines, sendRows.length]);

  useEffect(() => {
    const count = restored ? restored.lines.filter((line) => !isBlankRow(line)).length : 0;
    if (count) onNotice(`Восстановлена неотправленная заявка: ${positionsWord(count)}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function paste(index: number, key: GridKey, text: string) {
    const result = applyPaste(lines, index, key, text, emptyLine());
    if ("error" in result) { onError(result.error); return; }
    setLines(result.positions);
    setShowErrors(true);
    onError(result.dropped ? `Не вошло строк: ${result.dropped} — в одной заявке не больше ${GRID_LIMIT} позиций` : "");
    onNotice(
      `Вставлено позиций: ${result.filled}` +
        (result.unknown.length ? `. Не распознаны (пропущены): ${result.unknown.join(", ")}` : ""),
    );
  }

  useEffect(() => {
    if (!seed || seed.id === lastSeed.current) return;
    lastSeed.current = seed.id;
    paste(0, "name", seed.text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed]);

  function setLineField(index: number, key: GridKey, value: string) {
    setLines((current) => current.map((line, i) => {
      if (i !== index) return line;
      return { ...line, [key]: value };
    }));
  }

  function pickItem(index: number, item: ItemRow) {
    setLines((current) => current.map((line, i) => (i === index ? {
      ...line,
      name: item.name,
      design_article: item.part_number || line.design_article,
      unit: item.unit && units.includes(item.unit) ? item.unit : line.unit,
      item_type: item.item_type || line.item_type,
    } : line)));
  }

  const setH = (patch: Partial<Header>) => setHeader((current) => ({ ...current, ...patch }));

  async function submit() {
    onError("");
    setShowErrors(true);
    if (hasErrors(errors)) {
      const bad = errors.rows.filter((row) => Object.keys(row).length).length;
      onError(errors.project ?? errors.delivery_point ?? `${positionsWord(bad)} с ошибкой — исправьте отмеченные ячейки`);
      return;
    }
    if (!sendRows.length) { onError("Добавьте хотя бы одну позицию"); return; }
    setBusy(true);
    try {
      const result = await requestsApi.createMany(sendRows.map((line) => payloadV2(line, header)), creationKey.current);
      creationKey.current = crypto.randomUUID();
      setLines([emptyLine()]);
      setShowErrors(false);
      await onCreated(result.requests.map((item) => item.legacy_id));
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Не удалось создать заявку");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rg-form">
      <div className="rg-common rg-common-v2">
        <div className="rg-common-title">Общее для всех позиций <span>— по умолчанию для строк; своё значение в строке главнее</span></div>
        <div className="rg-common-grid">
          <label className="rg-project">Проект *
            <ComboBox
              strict
              aria-label="Проект"
              aria-invalid={showErrors && errors.project ? true : undefined}
              className={showErrors && errors.project ? "rg-bad" : undefined}
              options={projectOptions}
              value={header.project}
              placeholder="Номер проекта заказчика или внутренний проект"
              onChange={pickProject}
              onClear={() => setHeader((current) => chooseProject(current, ""))}
            />
            {header.purpose && <small className="rg-purpose muted">Назначение: {purposeLabel(header.purpose)}</small>}
            {showErrors && errors.project && <small className="rg-field-error" role="alert">{errors.project}</small>}
          </label>
          <label className="rg-point">Склад (куда доставить) *
            <select
              aria-label="Склад (куда доставить)"
              aria-invalid={showErrors && errors.delivery_point ? true : undefined}
              className={showErrors && errors.delivery_point ? "rg-bad" : undefined}
              value={header.delivery_point}
              onChange={(e) => setH({ delivery_point: e.target.value })}
            >
              <option value="">Выберите склад</option>
              {(schema?.delivery_points ?? []).map((point) => <option key={point} value={point}>{pointLabel(point)}</option>)}
            </select>
            {showErrors && errors.delivery_point && <small className="rg-field-error" role="alert">{errors.delivery_point}</small>}
          </label>
          <label>Док. качества
            <select aria-label="Док. качества" value={header.quality_document} onChange={(e) => setH({ quality_document: e.target.value })}>
              <option>Нет</option><option>Да</option>
            </select>
          </label>
          <label>План. дата
            <input aria-label="План. дата" type="date" placeholder="по умолчанию +14 дней" value={header.planned_date} onChange={(e) => setH({ planned_date: e.target.value })} />
            {!header.planned_date && <small>по умолчанию +14 дней</small>}
          </label>
          <label>Срок закупки
            <input aria-label="Срок закупки" type="date" placeholder="по умолчанию +7 дней" value={header.procurement_deadline} onChange={(e) => setH({ procurement_deadline: e.target.value })} />
            {!header.procurement_deadline && <small>по умолчанию +7 дней</small>}
          </label>
          <div className="rg-apply">
            <button type="button" className="secondary small" onClick={() => setLines((current) => applyHeaderToAll(current))}>Применить ко всем строкам</button>
          </div>
        </div>
      </div>
      <p className="rg-hint">Вставьте блок из Excel или Таблиц прямо в таблицу (Ctrl+V в любую ячейку) · Enter — вниз, Tab — вправо, на последней строке Enter добавляет строку · пустые док. качества и план. дата в строке берутся из шапки · в одной заявке до {GRID_LIMIT} позиций</p>
      <RequestGridV2
        positions={lines}
        header={header}
        schema={schema}
        errors={showErrors ? errors.rows : null}
        sendCount={sendRows.length}
        busy={busy}
        itemsOn
        onField={setLineField}
        onPickItem={pickItem}
        onPaste={paste}
        onAdd={() => setLines((current) => (current.length >= GRID_LIMIT ? current : [...current, emptyLine()]))}
        onRemove={(index) => setLines((current) => (current.length > 1 ? current.filter((_, i) => i !== index) : [emptyLine()]))}
        onDuplicate={(index) => setLines((current) => (current.length >= GRID_LIMIT ? current : [...current.slice(0, index + 1), { ...current[index] }, ...current.slice(index + 1)]))}
        onSubmit={() => void submit()}
      />
    </div>
  );
}
