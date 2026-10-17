import { useEffect, useMemo, useState, type FocusEvent, type KeyboardEvent } from "react";
import {
  applyE4ConnectorRowEdit,
  materializeE4ConnectorArticle,
  type E4ConnectorArticleTable,
  type E4ConnectorBaseColumnId,
  type E4ConnectorRowEditScope,
  type E4ConnectorRowOverride,
  type E4ConnectorSeriesTable,
  type MaterializedE4ConnectorArticleRow,
} from "./e4-connector-series-table";
import type { ArticleKeyV3 } from "./template-model-v3";
import "./E4ConnectorTableEditor.css";

export interface E4ConnectorTableEditorProps {
  readonly table: E4ConnectorSeriesTable;
  readonly selectedArticleVariantId: string | null | undefined;
  readonly onChange: (table: E4ConnectorSeriesTable) => void;
  readonly disabled?: boolean;
}

const COLUMN_CLASS: Readonly<Record<E4ConnectorBaseColumnId, string>> = {
  number: "number",
  name: "name",
  circuitText: "circuit",
  contactTypeGroupId: "type",
  standardTerminalArticleKey: "terminal",
};

const articleIdentity = (value: ArticleKeyV3): string =>
  `${value.sourceId}\u001f${value.entityType}\u001f${value.articleKey}`;

export function setE4ConnectorColumnVisibility(
  table: E4ConnectorSeriesTable,
  columnId: E4ConnectorBaseColumnId,
  visible: boolean,
): E4ConnectorSeriesTable {
  if (!table.columns.some(column => column.id === columnId)) return table;
  return {
    ...table,
    columns: table.columns.map(column => column.id === columnId ? { ...column, visible } : column),
  };
}

export function updateE4ConnectorTableCell(
  table: E4ConnectorSeriesTable,
  articleVariantId: string,
  seriesRowId: string,
  scope: E4ConnectorRowEditScope,
  changes: E4ConnectorRowOverride,
): E4ConnectorSeriesTable {
  return applyE4ConnectorRowEdit(table, { articleVariantId, seriesRowId, scope, changes });
}

function simpleError(caught: unknown): string {
  if (!(caught instanceof Error)) return "Не удалось изменить таблицу Э4.";
  if (caught.message.includes("не найден")) return "Строка или артикул больше не существует. Обновите выбор.";
  if (caught.message.includes("непустым текстом")) return "Поле нельзя оставить пустым.";
  return caught.message || "Не удалось изменить таблицу Э4.";
}

function commitOnEnter(event: KeyboardEvent<HTMLInputElement>): void {
  if (event.key === "Enter") event.currentTarget.blur();
}

interface TextCellProps {
  readonly value: string;
  readonly label: string;
  readonly disabled: boolean;
  readonly onCommit: (value: string) => void;
}

function TextCell({ value, label, disabled, onCommit }: TextCellProps) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const commit = (event: FocusEvent<HTMLInputElement>) => {
    if (event.currentTarget.value !== value) onCommit(event.currentTarget.value);
  };
  return <input
    type="text"
    aria-label={label}
    value={draft}
    onChange={event => setDraft(event.target.value)}
    disabled={disabled}
    onBlur={commit}
    onKeyDown={commitOnEnter}
  />;
}

function compatibleTerminals(article: E4ConnectorArticleTable, groupId: string | null): readonly ArticleKeyV3[] {
  if (groupId === null) return [];
  return article.contactGroups.find(group => group.contactTypeGroupId === groupId)?.allowedTerminalArticleKeys ?? [];
}

export function E4ConnectorTableEditor(props: E4ConnectorTableEditorProps) {
  const [scope, setScope] = useState<E4ConnectorRowEditScope>("article");
  const [error, setError] = useState<string | null>(null);
  const article = props.table.articles.find(item => item.articleVariantId === props.selectedArticleVariantId) ?? null;
  const materialized = useMemo(() => {
    if (!article) return null;
    try { return materializeE4ConnectorArticle(props.table, article.articleVariantId); }
    catch { return null; }
  }, [article, props.table]);
  const visibleColumns = props.table.columns.filter(column => column.visible);

  const apply = (
    row: MaterializedE4ConnectorArticleRow,
    changes: E4ConnectorRowOverride,
    editScope: E4ConnectorRowEditScope = scope,
  ) => {
    if (!article || props.disabled) return;
    try {
      props.onChange(updateE4ConnectorTableCell(
        props.table, article.articleVariantId, row.seriesRowId, editScope, changes,
      ));
      setError(null);
    } catch (caught) { setError(simpleError(caught)); }
  };

  const toggleColumn = (columnId: E4ConnectorBaseColumnId, visible: boolean) => {
    if (props.disabled) return;
    props.onChange(setE4ConnectorColumnVisibility(props.table, columnId, visible));
  };

  return <section className="e4-table-editor" aria-label="Таблица соединителя Э4">
    <header className="e4-table-editor__toolbar">
      <div>
        <strong>Таблица Э4</strong>
        {article && <span className="e4-table-editor__article">{article.articleKey}</span>}
      </div>
      <label>
        Изменять
        <select
          aria-label="Область применения правки"
          value={scope}
          disabled={props.disabled}
          onChange={event => setScope(event.currentTarget.value as E4ConnectorRowEditScope)}
        >
          <option value="article">только этот артикул</option>
          <option value="series">всю серию</option>
        </select>
      </label>
    </header>

    <div className="e4-table-editor__columns" aria-label="Видимость колонок">
      {props.table.columns.map(column => <label key={column.id}>
        <input
          type="checkbox"
          checked={column.visible}
          disabled={props.disabled}
          onChange={event => toggleColumn(column.id, event.currentTarget.checked)}
        />
        {column.label}
      </label>)}
    </div>

    {error && <p className="e4-table-editor__error" role="alert">{error}</p>}
    {!article && <p className="e4-table-editor__empty">Выберите артикул серии.</p>}
    {article && !materialized && <p className="e4-table-editor__error" role="alert">Не удалось открыть таблицу этого артикула.</p>}
    {materialized && article && <div className="e4-table-editor__scroll" tabIndex={0}>
      <table>
        <thead><tr>{visibleColumns.map(column =>
          <th key={column.id} className={`e4-table-editor__${COLUMN_CLASS[column.id]}`}>{column.label}</th>)}</tr></thead>
        <tbody>{materialized.rows.map((row, rowIndex) => {
          const terminals = compatibleTerminals(article, row.contactTypeGroupId);
          const terminalValue = row.standardTerminalArticleKey === null ? "" : articleIdentity(row.standardTerminalArticleKey);
          return <tr key={`${article.articleVariantId}:${row.seriesRowId}`}>
            {visibleColumns.map(column => <td key={column.id} className={`e4-table-editor__${COLUMN_CLASS[column.id]}`}>
              {column.id === "number" && <TextCell value={row.number} label={`Номер контакта, строка ${rowIndex + 1}`} disabled={Boolean(props.disabled)} onCommit={value => apply(row, { number: value })} />}
              {column.id === "name" && <TextCell value={row.name} label={`Назначение, строка ${rowIndex + 1}`} disabled={Boolean(props.disabled)} onCommit={value => apply(row, { name: value })} />}
              {column.id === "circuitText" && <TextCell value={row.circuitText ?? ""} label={`Цепь, строка ${rowIndex + 1}`} disabled={Boolean(props.disabled)} onCommit={value => apply(row, { circuitText: value || null })} />}
              {column.id === "contactTypeGroupId" && <select
                aria-label={`Тип контакта, строка ${rowIndex + 1}`}
                value={row.contactTypeGroupId ?? ""}
                disabled={props.disabled}
                onChange={event => apply(row, {
                  contactTypeGroupId: event.currentTarget.value || null,
                  standardTerminalArticleKey: null,
                })}
              >
                <option value="">Не выбран</option>
                {props.table.contactTypeGroups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}
              </select>}
              {column.id === "standardTerminalArticleKey" && <select
                aria-label={`Стандартный контакт, строка ${rowIndex + 1}`}
                title="Стандартный терминал задаётся отдельно для выбранного артикула"
                value={terminalValue}
                disabled={props.disabled || row.contactTypeGroupId === null}
                onChange={event => apply(row, {
                  standardTerminalArticleKey: terminals.find(item => articleIdentity(item) === event.currentTarget.value) ?? null,
                }, "article")}
              >
                <option value="">Не выбран</option>
                {terminals.map(terminal => <option key={articleIdentity(terminal)} value={articleIdentity(terminal)}>{terminal.articleKey}</option>)}
              </select>}
            </td>)}</tr>;
        })}</tbody>
      </table>
      {materialized.rows.length === 0 && <p className="e4-table-editor__empty">У артикула пока нет контактов.</p>}
    </div>}
  </section>;
}
