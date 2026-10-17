import { useEffect, useMemo, useState, type ReactNode, type FormEvent } from "react";
import { expandArticlePattern } from "./article-pattern-expansion";
import type { ArticleKeyV3, TemplateContentV3 } from "./template-model-v3";
import { articleContactCountIssueV3, articleContactCountRuleV3, TEMPLATE_V3_LIMITS } from "./template-model-v3";
import { materializeArticleContactRowsV3 } from "./template-article-contact-rows-v3";
import "./TemplateSeriesPanelV3.css";
import { InfoHint } from "../InfoHint";
import { AnchoredPopover } from "../AnchoredPopover";

export interface NewArticleVariantV3Input {
  readonly sourceId: string;
  readonly entityType: string;
  readonly articleKey: string;
  readonly contactCount?: number;
}

export const CONNECTOR_ARTICLE_SOURCE_V3 = "БД.СОЕД";
export const CONNECTOR_ARTICLE_ENTITY_V3 = "connector";
export const CONNECTOR_REFERENCE_SOURCE_V3 = "technology-connectors";
export const TERMINAL_REFERENCE_SOURCE_V3 = "technology-terminals";
export const TERMINAL_ARTICLE_ENTITY_V3 = "terminal";
export const CONTACT_TYPE_PRESETS_V3 = ["Сигнальные", "Силовые", "Дополнительные"] as const;
export const CUSTOM_CONTACT_TYPE_V3 = "__custom__";
export type ArticleAddModeV3 = "single" | "pattern";

/** Contact type names are unique within a connector series, ignoring case and surrounding spaces. */
export function contactTypeNameExistsV3(
  content: Pick<TemplateContentV3, "contactTypeGroups">,
  name: string,
): boolean {
  const normalized = name.trim().toLocaleLowerCase("ru-RU");
  return Boolean(normalized) && content.contactTypeGroups.some(
    group => group.name.trim().toLocaleLowerCase("ru-RU") === normalized,
  );
}

export function patternContactCount(pattern: string, article: string): number {
  const match = /X{2,}/.exec(pattern.trim());
  if (!match) throw new Error("В шаблоне отсутствует XX.");
  const suffixLength = pattern.trim().length - match.index - match[0].length;
  const count = Number(article.slice(match.index, suffixLength ? -suffixLength : undefined));
  if (!Number.isSafeInteger(count) || count < 0 || count > 2000) throw new Error("Количество пинов из шаблона должно быть от 0 до 2000.");
  return count;
}

export interface ArticleAddPreviewV3 {
  readonly articles: readonly string[];
  readonly error: string | null;
}

/** Validates one manual article or a generated set against the current series. */
export function articleAddPreviewV3(
  content: Pick<TemplateContentV3, "articleVariants">,
  mode: ArticleAddModeV3,
  articleKey: string,
  pattern: string,
  variables: string,
): ArticleAddPreviewV3 {
  const remaining = TEMPLATE_V3_LIMITS.articleVariants - content.articleVariants.length;
  if (remaining < 1) return { articles: [], error: "В серии уже достигнут лимит артикулов." };

  let articles: readonly string[];
  if (mode === "single") {
    const normalized = articleKey.trim();
    if (!normalized) return { articles: [], error: null };
    if (normalized.length > 512 || /[\u0000-\u001f\u007f-\u009f]/.test(normalized))
      return { articles: [], error: "Артикул должен быть текстом не длиннее 512 символов." };
    articles = [normalized];
  } else {
    if (!pattern.trim() && !variables.trim()) return { articles: [], error: null };
    try {
      articles = expandArticlePattern(pattern, variables, { maximumArticleCount: remaining });
      articles.forEach(article => patternContactCount(pattern, article));
    } catch (caught) {
      return {
        articles: [],
        error: caught instanceof Error ? caught.message : "Не удалось проверить шаблон артикулов.",
      };
    }
  }

  const existing = new Set(content.articleVariants
    .filter(variant => variant.entityType === CONNECTOR_ARTICLE_ENTITY_V3)
    .map(variant => variant.articleKey));
  const collision = articles.find(article => existing.has(article));
  return collision
    ? { articles: [], error: `Артикул «${collision}» уже есть в этой серии.` }
    : { articles, error: null };
}

export interface TemplateSeriesPanelV3Props {
  readonly articlePreview?: ReactNode;
  readonly independentE4?: boolean;
  readonly content: TemplateContentV3;
  readonly selectedArticleVariantId?: string | null;
  readonly onSelectArticleVariant?: (variantId: string | null) => void;
  readonly terminalArticleQuery?: string;
  readonly terminalArticleSuggestions?: readonly ArticleKeyV3[];
  readonly terminalArticleSearchState?: "idle" | "loading" | "ready" | "error";
  readonly terminalArticleSearchMessage?: string | null;
  readonly onTerminalArticleQueryChange?: (query: string) => void;
  /** One compatibility list for the whole connector series (schema v5). */
  readonly compatibleTerminalArticleKeys?: readonly ArticleKeyV3[];
  readonly onChangeCompatibleTerminalArticleKeys?: (keys: readonly ArticleKeyV3[]) => void;
  readonly terminalContactTypeGroupIds?: Readonly<Record<string, string | null>>;
  readonly onSetTerminalContactTypeGroup?: (terminal: ArticleKeyV3, groupId: string | null) => void;
  readonly standardTerminalIdentities?: ReadonlySet<string>;
  readonly onSetSeriesStandardTerminal?: (terminal: ArticleKeyV3, standard: boolean) => void;
  readonly onAddContactTypeGroup: (name: string) => void;
  readonly onRenameContactTypeGroup: (groupId: string, name: string) => void;
  readonly onDeleteContactTypeGroup: (groupId: string) => void;
  /** Returns true only after the entire batch was accepted. */
  readonly onAddArticleVariants: (inputs: readonly NewArticleVariantV3Input[]) => boolean;
  readonly onUpdateArticleVariant?: (variantId: string, input: NewArticleVariantV3Input) => void;
  readonly onDeleteArticleVariant: (variantId: string) => void;
  readonly onSetArticleContactGroup: (
    variantId: string,
    contactTypeGroupId: string,
    contactCount: number,
    allowedTerminalArticleKeys: readonly ArticleKeyV3[],
  ) => void;
  readonly onRemoveArticleContactGroup: (variantId: string, contactTypeGroupId: string) => void;
  readonly standardTerminalArticleKeys?: Readonly<Record<string, ArticleKeyV3 | null>>;
  readonly onSetStandardTerminal?: (variantId: string, contactTypeGroupId: string, terminal: ArticleKeyV3 | null) => void;
}

export const standardTerminalKeyV3 = (variantId: string, groupId: string): string => `${variantId}\0${groupId}`;

export function parseTerminalArticleKeysV3(value: string): readonly ArticleKeyV3[] | null {
  const rows: string[][] = [[]];
  let escaped = false;
  for (let index = 0; index < value.length; index += 1) {
    const character = value[index]!;
    if (escaped) {
      if (character === "\\" || character === "|" || character === ";") rows.at(-1)!.push(character);
      else rows.at(-1)!.push("\\", character);
      escaped = false;
    } else if (character === "\\") escaped = true;
    else if (character === "|") rows.push([]);
    else if (character === ";" || character === "\n") rows.push([]);
    else if (character !== "\r") rows.at(-1)!.push(character);
  }
  if (escaped) rows.at(-1)!.push("\\");
  const lines = rows.map(part => part.join("").trim()).filter(Boolean);
  if (lines.length % 3 !== 0) return null;
  const result: ArticleKeyV3[] = [], seen = new Set<string>();
  for (let index = 0; index < lines.length; index += 3) {
    const parts = lines.slice(index, index + 3);
    if (parts.some(part => part.length > 512 || /[\u0000-\u001f]/.test(part))) return null;
    const key = { sourceId: parts[0]!, entityType: parts[1]!, articleKey: parts[2]! };
    if (key.sourceId.length > 128 || key.entityType.length > 64) return null;
    const identity = `${key.sourceId}\0${key.entityType}\0${key.articleKey}`;
    if (seen.has(identity)) return null;
    seen.add(identity); result.push(key);
  }
  return result;
}

export function formatTerminalArticleKeysV3(keys: readonly ArticleKeyV3[]): string {
  const escape = (value: string) => value.replace(/\\/g, "\\\\").replace(/\|/g, "\\|").replace(/;/g, "\\;");
  return keys.map(key => `${escape(key.sourceId)}|${escape(key.entityType)}|${escape(key.articleKey)}`).join("\n");
}

export interface TerminalArticleKeyPatchV3 {
  readonly sourceId?: string;
  readonly entityType?: string;
  readonly articleKey?: string;
}

/** Updates one structured terminal row and returns null while it is incomplete or duplicated. */
export function updateTerminalArticleKeyV3(
  keys: readonly ArticleKeyV3[],
  index: number,
  patch: TerminalArticleKeyPatchV3,
): readonly ArticleKeyV3[] | null {
  if (!Number.isSafeInteger(index) || index < 0 || index >= keys.length) return null;
  const current = keys[index]!;
  const next = {
    sourceId: (patch.sourceId ?? current.sourceId).trim(),
    entityType: (patch.entityType ?? current.entityType).trim(),
    articleKey: (patch.articleKey ?? current.articleKey).trim(),
  };
  if (!next.sourceId || !next.entityType || !next.articleKey || next.sourceId.length > 128 || next.entityType.length > 64 || next.articleKey.length > 512 ||
      [next.sourceId, next.entityType, next.articleKey].some(value => /[\u0000-\u001f]/.test(value))) return null;
  const identity = `${next.sourceId}\0${next.entityType}\0${next.articleKey}`;
  if (keys.some((key, candidateIndex) => candidateIndex !== index && `${key.sourceId}\0${key.entityType}\0${key.articleKey}` === identity)) return null;
  return keys.map((key, candidateIndex) => candidateIndex === index ? next : { ...key });
}

export function appendTerminalArticleKeyV3(
  keys: readonly ArticleKeyV3[],
  value: ArticleKeyV3,
): readonly ArticleKeyV3[] | null {
  const next = updateTerminalArticleKeyV3([...keys, { sourceId: "", entityType: "", articleKey: "" }], keys.length, value);
  return next;
}

export interface ArticleContactGroupEditorValueV3 {
  readonly contactCount: number;
  readonly inherited: boolean;
  readonly configured: boolean;
  readonly allowedTerminalArticleKeys: readonly ArticleKeyV3[];
}

export function articleContactGroupEditorValueV3(
  content: TemplateContentV3,
  variantId: string,
  groupId: string,
): ArticleContactGroupEditorValueV3 {
  const variant = content.articleVariants.find(candidate => candidate.id === variantId);
  const configured = variant?.contactGroups?.find(group => group.contactTypeGroupId === groupId);
  if (configured) return {
    contactCount: configured.contactCount,
    inherited: false,
    configured: true,
    allowedTerminalArticleKeys: configured.allowedTerminalArticleKeys,
  };
  if (!variant || variant.contactGroups !== null)
    return { contactCount: 0, inherited: false, configured: false, allowedTerminalArticleKeys: [] };
  try {
    return {
      contactCount: materializeArticleContactRowsV3(content, variant)
        .filter(row => row.contactTypeGroupId === groupId).length,
      inherited: true,
      configured: false,
      allowedTerminalArticleKeys: [],
    };
  } catch {
    return { contactCount: 0, inherited: true, configured: false, allowedTerminalArticleKeys: [] };
  }
}

export function articleContactGroupCountHelpV3(
  content: TemplateContentV3,
  groupId: string,
  requested: number,
  inherited: boolean,
): { readonly text: string; readonly invalid: boolean } {
  const issue = inherited ? null : articleContactCountIssueV3(content, groupId, requested);
  if (issue) return { text: issue, invalid: true };
  const rule = articleContactCountRuleV3(content, groupId);
  const source = rule.repeatStrides.length === 0
    ? `Сейчас допустимо только значение ${rule.fixedContactCount}. Другое количество требует прототипа контакта и домена повтора этой группы.`
    : rule.repeatStrides.length === 1
      ? `Итог состоит из ${rule.fixedContactCount} фиксированных контактов и повторяемого сегмента по ${rule.repeatStrides[0]} контакта.`
      : "Для группы найдено несколько доменов повтора; перед сохранением оставьте один.";
  return {
    text: inherited ? `Количество наследуется из шаблона. ${source}` : source,
    invalid: rule.repeatStrides.length > 1,
  };
}

function GroupNameEditor({ id, name, onRename, onDelete }: {
  readonly id: string; readonly name: string;
  readonly onRename: (id: string, name: string) => void;
  readonly onDelete: (id: string) => void;
}) {
  const [value, setValue] = useState(name);
  useEffect(() => setValue(name), [id, name]);
  const save = () => { const normalized = value.trim(); if (normalized && normalized !== name) onRename(id, normalized); else setValue(name); };
  return <li>
    <input aria-label={`Название группы ${name}`} value={value} onChange={event => setValue(event.target.value)} onBlur={save} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); save(); } }} />
    <button type="button" onClick={() => onDelete(id)} aria-label={`Удалить группу ${name}`}>×</button>
  </li>;
}

function terminalIdentity(terminal: ArticleKeyV3): string {
  return `${terminal.sourceId}\0${terminal.entityType}\0${terminal.articleKey}`;
}

export function readableTerminalArticleV3(articleKey: string): string {
  const parts: string[] = [];
  let offset = 0;
  while (offset < articleKey.length) {
    const separator = articleKey.indexOf(":", offset);
    if (separator < 0 || !/^\d+$/.test(articleKey.slice(offset, separator))) return articleKey;
    const length = Number(articleKey.slice(offset, separator));
    const end = separator + 1 + length;
    if (!Number.isSafeInteger(length) || end > articleKey.length) return articleKey;
    parts.push(articleKey.slice(separator + 1, end));
    if (end === articleKey.length) break;
    if (articleKey[end] !== "|") return articleKey;
    offset = end + 1;
  }
  const readable = [parts[0], parts[1], parts[3]].filter(Boolean).join(" ");
  return readable || articleKey;
}

function SeriesTerminalRow({ terminal, index, groups, groupId, standard, onSetGroup, onSetStandard, onChange, onRemove }: {
  readonly terminal: ArticleKeyV3;
  readonly index: number;
  readonly groups: TemplateContentV3["contactTypeGroups"];
  readonly groupId: string | null;
  readonly standard: boolean;
  readonly onSetGroup?: (terminal: ArticleKeyV3, groupId: string | null) => void;
  readonly onSetStandard?: (terminal: ArticleKeyV3, standard: boolean) => void;
  readonly onChange: (index: number, patch: TerminalArticleKeyPatchV3) => void;
  readonly onRemove: (index: number) => void;
}) {
  const [draft, setDraft] = useState(terminal);
  useEffect(() => setDraft(terminal), [terminal.sourceId, terminal.entityType, terminal.articleKey]);
  const commit = () => onChange(index, draft);
  const readableArticle = readableTerminalArticleV3(draft.articleKey);
  return <tr>
    <td>{readableArticle !== draft.articleKey
      ? <span className="series-v3-terminal-article" title={draft.articleKey}>{readableArticle}</span>
      : <input aria-label={`Артикул терминала ${index + 1}`} value={draft.articleKey}
        onChange={event => setDraft(current => ({ ...current, articleKey: event.target.value }))} onBlur={commit} />}</td>
    <td><select aria-label={`Тип контакта терминала ${index + 1}`} value={groupId ?? ""} disabled={!onSetGroup}
      onChange={event => onSetGroup?.(terminal, event.currentTarget.value || null)}>
      <option value="">Не назначен</option>
      {groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}
    </select></td>
    <td className="series-v3-standard-cell"><input type="checkbox" aria-label={`Стандартный терминал ${index + 1}`} checked={standard}
      disabled={!onSetStandard || groupId === null}
      onChange={event => onSetStandard?.(terminal, event.currentTarget.checked)} /></td>
    <td><button type="button" className="series-v3-remove-terminal" aria-label={`Удалить терминал ${index + 1}`} onClick={() => onRemove(index)}>×</button></td>
  </tr>;
}

function SeriesTerminalEditor({ terminals, groups, terminalContactTypeGroupIds, standardTerminalIdentities, onSetTerminalContactTypeGroup, onSetSeriesStandardTerminal, onChange, terminalQuery,
  terminalSuggestions, terminalSearchState, terminalSearchMessage, onTerminalQueryChange }: {
  readonly terminals: readonly ArticleKeyV3[];
  readonly groups: TemplateContentV3["contactTypeGroups"];
  readonly terminalContactTypeGroupIds: Readonly<Record<string, string | null>>;
  readonly standardTerminalIdentities: ReadonlySet<string>;
  readonly onSetTerminalContactTypeGroup?: (terminal: ArticleKeyV3, groupId: string | null) => void;
  readonly onSetSeriesStandardTerminal?: (terminal: ArticleKeyV3, standard: boolean) => void;
  readonly onChange?: (keys: readonly ArticleKeyV3[]) => void;
  readonly terminalQuery: string;
  readonly terminalSuggestions: readonly ArticleKeyV3[];
  readonly terminalSearchState: "idle" | "loading" | "ready" | "error";
  readonly terminalSearchMessage: string | null;
  readonly onTerminalQueryChange?: (query: string) => void;
}) {
  const setTerminalField = (index: number, patch: TerminalArticleKeyPatchV3) => {
    const next = updateTerminalArticleKeyV3(terminals, index, patch);
    if (next) onChange?.(next);
  };
  const removeTerminal = (index: number) => {
    onChange?.(terminals.filter((_, candidateIndex) => candidateIndex !== index));
  };
  const addTerminal = () => {
    const next = appendTerminalArticleKeyV3(terminals, { sourceId: TERMINAL_REFERENCE_SOURCE_V3, entityType: TERMINAL_ARTICLE_ENTITY_V3, articleKey: `Новый-${terminals.length + 1}` });
    if (next) onChange?.(next);
  };
  const addTerminalFromReference = (terminal: ArticleKeyV3) => {
    const next = appendTerminalArticleKeyV3(terminals, terminal);
    if (next) onChange?.(next);
  };
  return <section className="series-v3-series-terminals" aria-label="Совместимые терминалы серии">
    <header><div><strong>Совместимые терминалы серии</strong><InfoHint>Назначьте тип контакта и стандартный терминал для серии. Ручные правки терминалов в строках артикулов сохраняются.</InfoHint></div></header>
    <div className="series-v3-terminal-reference-search">
      <label>Добавить терминал
        <input type="search" value={terminalQuery} placeholder="Например, SXH-001T"
          onChange={event => onTerminalQueryChange?.(event.target.value)} />
      </label>
      <button type="button" disabled={!onChange} onClick={addTerminal}>+ Вручную</button>
      {terminalSearchState === "loading" && <small role="status">Ищем терминалы…</small>}
      {terminalSearchMessage && <small className="series-v3-reference-error" role="alert">{terminalSearchMessage}</small>}
      {terminalSearchState === "ready" && terminalQuery.trim() && terminalSuggestions.length === 0 && <small>Подходящие терминалы не найдены.</small>}
      {terminalSuggestions.length > 0 && <AnchoredPopover className="series-v3-terminal-reference-options" role="listbox" label="Терминалы из справочника для серии" open onClose={() => onTerminalQueryChange?.("")}>
        {terminalSuggestions.filter(terminal => !terminals.some(candidate => terminalIdentity(candidate) === terminalIdentity(terminal))).map(terminal => {
          const exists = terminals.some(candidate => terminalIdentity(candidate) === terminalIdentity(terminal));
          return <button type="button" key={terminalIdentity(terminal)} disabled={exists}
            onClick={() => addTerminalFromReference(terminal)}>
            <strong>{readableTerminalArticleV3(terminal.articleKey)}</strong><span>{exists ? "добавлен" : "+ допустимый"}</span>
          </button>;
        })}
      </AnchoredPopover>}
    </div>
    {terminals.length === 0 ? <small>Совместимые терминалы пока не заданы.</small> : <div className="series-v3-terminal-table-wrap"><table className="series-v3-terminal-table">
      <thead><tr><th>Артикул</th><th>Тип контакта</th><th>Стандартный</th><th aria-label="Удалить" /></tr></thead>
      <tbody>{terminals.map((terminal, index) => <SeriesTerminalRow key={terminalIdentity(terminal)} terminal={terminal} index={index}
        groups={groups} groupId={terminalContactTypeGroupIds[terminalIdentity(terminal)] ?? null}
        standard={standardTerminalIdentities.has(terminalIdentity(terminal))}
        onSetGroup={onSetTerminalContactTypeGroup} onSetStandard={onSetSeriesStandardTerminal} onChange={setTerminalField} onRemove={removeTerminal} />)}</tbody>
    </table></div>}
  </section>;
}

function VariantIdentityEditor({ variant, onUpdate }: {
  readonly variant: TemplateContentV3["articleVariants"][number];
  readonly onUpdate?: TemplateSeriesPanelV3Props["onUpdateArticleVariant"];
}) {
  const [articleKey, setArticleKey] = useState(variant.articleKey);
  useEffect(() => {
    setArticleKey(variant.articleKey);
  }, [variant.id, variant.articleKey]);
  const save = () => {
    const input = { sourceId: CONNECTOR_ARTICLE_SOURCE_V3, entityType: CONNECTOR_ARTICLE_ENTITY_V3, articleKey: articleKey.trim() };
    if (!onUpdate || !input.articleKey) return;
    if (input.sourceId !== variant.sourceId || input.entityType !== variant.entityType || input.articleKey !== variant.articleKey)
      onUpdate(variant.id, input);
  };
  return <input className="series-v3-article-key" aria-label={`Артикул ${variant.articleKey}`} value={articleKey}
    onChange={event => setArticleKey(event.target.value)} onBlur={save}
    onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); } }} />;
}

function ContactCountInput({ value, label, onFocus, onCommit, title, invalid }: {
  value: number; label: string; onFocus: () => void; onCommit: (value: number) => void; title: string; invalid: boolean;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const commit = () => {
    const count = Number(draft);
    if (draft.trim() && Number.isSafeInteger(count) && count >= 0 && count <= 2000) {
      if (count !== value) onCommit(count);
    } else setDraft(String(value));
  };
  return <input type="number" min="0" max="2000" step="1" value={draft} aria-label={label} aria-invalid={invalid || undefined} title={title}
    onFocus={onFocus} onChange={event => setDraft(event.target.value)} onBlur={commit}
    onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); }} />;
}

function ArticleVariantsTable({ independentE4, content, compatibleTerminals, selectedArticleVariantId, onSelect, onUpdate, onDelete, onSet }: {
  readonly independentE4?: boolean;
  /** Deprecated compatibility inputs; the series editor no longer renders a connector search. */
  readonly articlePreviewMessage?: string | null;
  readonly articlePreviewError?: string | null;
  readonly articlePreviewRows?: readonly unknown[];
  readonly connectorArticleQuery?: string;
  readonly connectorArticleSuggestions?: readonly NewArticleVariantV3Input[];
  readonly connectorArticleSearchState?: "idle" | "loading" | "ready" | "error";
  readonly connectorArticleSearchMessage?: string | null;
  readonly onConnectorArticleQueryChange?: (query: string) => void;
  readonly content: TemplateContentV3;
  readonly compatibleTerminals: readonly ArticleKeyV3[];
  readonly selectedArticleVariantId?: string | null;
  readonly onSelect?: (variantId: string | null) => void;
  readonly onUpdate?: TemplateSeriesPanelV3Props["onUpdateArticleVariant"];
  readonly onDelete: TemplateSeriesPanelV3Props["onDeleteArticleVariant"];
  readonly onSet: TemplateSeriesPanelV3Props["onSetArticleContactGroup"];
}) {
  return <div className="series-v3-article-table-wrap">
    <table className="series-v3-article-table" aria-label="Таблица артикулов серии">
      <thead><tr>
        <th>Артикул</th>
        {content.contactTypeGroups.map(group => <th key={group.id}>{group.name}, шт.</th>)}
        <th aria-label="Действия" />
      </tr></thead>
      <tbody>{content.articleVariants.map(variant => <tr key={variant.id}
        className={variant.id === selectedArticleVariantId ? "selected" : undefined}
        data-article-variant-id={variant.id}
        aria-label={`Открыть таблицу Э4 артикула ${variant.articleKey}`}
        tabIndex={0}
        onClick={() => onSelect?.(variant.id)}
        onFocus={() => onSelect?.(variant.id)}
        onKeyDown={event => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          onSelect?.(variant.id);
        }}>
        <td><div className="series-v3-article-identity"><button type="button" className="series-v3-select-article" aria-label={`Открыть артикул ${variant.articleKey}`} aria-pressed={variant.id === selectedArticleVariantId} onClick={() => onSelect?.(variant.id)}>▸</button><VariantIdentityEditor variant={variant} onUpdate={onUpdate} /></div></td>
        {content.contactTypeGroups.map(group => {
          const editor = articleContactGroupEditorValueV3(content, variant.id, group.id);
          const help = independentE4 ? { invalid: false, text: "Количество строк Э4 от 0 до 2000; графика задаётся отдельно." } : articleContactGroupCountHelpV3(content, group.id, editor.contactCount, editor.inherited);
          return <td key={group.id} className="series-v3-count-cell">
            <ContactCountInput value={editor.contactCount}
              label={`Количество контактов ${group.name} артикула ${variant.articleKey}`}
              invalid={help.invalid} title={help.text}
              onFocus={() => onSelect?.(variant.id)}
              onCommit={count => onSet(variant.id, group.id, count, compatibleTerminals)} />
          </td>;
        })}
        <td className="series-v3-row-actions">
          <button type="button" className="series-v3-delete-article" aria-label={`Удалить артикул ${variant.articleKey}`}
            onClick={event => { event.stopPropagation(); onDelete(variant.id); }}>×</button>
        </td>
      </tr>)}</tbody>
    </table>
  </div>;
}

export function TemplateSeriesPanelV3(props: TemplateSeriesPanelV3Props) {
  const [contactTypeChoice, setContactTypeChoice] = useState<string>(CONTACT_TYPE_PRESETS_V3[0]);
  const [customContactTypeName, setCustomContactTypeName] = useState("");
  const [articleAddMode, setArticleAddMode] = useState<ArticleAddModeV3>("single");
  const [articleKey, setArticleKey] = useState("");
  const [articlePattern, setArticlePattern] = useState("");
  const [articleVariables, setArticleVariables] = useState("");
  const addPreview = useMemo(() => articleAddPreviewV3(
    props.content,
    articleAddMode,
    articleKey,
    articlePattern,
    articleVariables,
  ), [props.content, articleAddMode, articleKey, articlePattern, articleVariables]);
  const pendingContactTypeName = contactTypeChoice === CUSTOM_CONTACT_TYPE_V3
    ? customContactTypeName.trim()
    : contactTypeChoice;
  const duplicateContactType = contactTypeNameExistsV3(props.content, pendingContactTypeName);
  const addGroup = (event: FormEvent) => {
    event.preventDefault();
    if (!pendingContactTypeName || duplicateContactType) return;
    props.onAddContactTypeGroup(pendingContactTypeName);
    if (contactTypeChoice === CUSTOM_CONTACT_TYPE_V3) setCustomContactTypeName("");
  };
  const addVariants = (event: FormEvent) => {
    event.preventDefault();
    if (addPreview.error || addPreview.articles.length === 0) return;
    const inputs = addPreview.articles.map(value => ({
      sourceId: CONNECTOR_ARTICLE_SOURCE_V3,
      entityType: CONNECTOR_ARTICLE_ENTITY_V3,
      articleKey: value,
      ...(articleAddMode === "pattern" ? { contactCount: patternContactCount(articlePattern, value) } : {}),
    }));
    const accepted = props.onAddArticleVariants(inputs);
    if (!accepted) return;
    if (articleAddMode === "single") setArticleKey("");
    else setArticleVariables("");
  };
  return <section className="template-series-v3" aria-label="Настройка серии">
    <div className="template-series-v3-body">
      <section className="series-v3-groups" aria-label="Группы типов контактов">
        <header><strong>Типы контактов</strong><InfoHint>Группы контактов серии: сигнальные, силовые и другие.</InfoHint></header>
        <form className="series-v3-add-contact-type" onSubmit={addGroup}>
          <label>Тип контакта<select aria-label="Новый тип контакта" value={contactTypeChoice} onChange={event => setContactTypeChoice(event.target.value)}>
            {CONTACT_TYPE_PRESETS_V3.map(preset => <option key={preset} value={preset}>{preset}</option>)}
            <option value={CUSTOM_CONTACT_TYPE_V3}>Другой тип</option>
          </select></label>
          {contactTypeChoice === CUSTOM_CONTACT_TYPE_V3 && <label>Название типа<input value={customContactTypeName} onChange={event => setCustomContactTypeName(event.target.value)} placeholder="Например, коаксиальные" aria-label="Название другого типа контакта" /></label>}
          <button type="submit" disabled={!pendingContactTypeName || duplicateContactType}>+ Тип</button>
          {duplicateContactType && <p className="series-v3-group-status" role="status">Тип «{pendingContactTypeName}» уже добавлен.</p>}
        </form>
        {props.content.contactTypeGroups.length ? <ul>{props.content.contactTypeGroups.map(group => <GroupNameEditor key={group.id} id={group.id} name={group.name} onRename={props.onRenameContactTypeGroup} onDelete={props.onDeleteContactTypeGroup} />)}</ul> : <p>Типы контактов ещё не добавлены.</p>}
      </section>
      <SeriesTerminalEditor
        terminals={props.compatibleTerminalArticleKeys ?? []}
        groups={props.content.contactTypeGroups}
        terminalContactTypeGroupIds={props.terminalContactTypeGroupIds ?? {}}
        standardTerminalIdentities={props.standardTerminalIdentities ?? new Set<string>()}
        onSetTerminalContactTypeGroup={props.onSetTerminalContactTypeGroup}
        onSetSeriesStandardTerminal={props.onSetSeriesStandardTerminal}
        onChange={props.onChangeCompatibleTerminalArticleKeys}
        terminalQuery={props.terminalArticleQuery ?? ""}
        terminalSuggestions={props.terminalArticleSuggestions ?? []}
        terminalSearchState={props.terminalArticleSearchState ?? "idle"}
        terminalSearchMessage={props.terminalArticleSearchMessage ?? null}
        onTerminalQueryChange={props.onTerminalArticleQueryChange}
      />
      <section className="series-v3-article-workspace" aria-label="Артикулы и представления"><section className="series-v3-variants" aria-label="Артикулы серии">
        <header><strong>Артикулы серии</strong><InfoHint>Каждый артикул задаёт число контактов по группам.{props.independentE4 && " Количество строк Э4 — от 0 до 2000; графика задаётся отдельно."}</InfoHint></header>
        <form className="series-v3-add-variant" onSubmit={addVariants}>
          <fieldset className="series-v3-add-mode">
            <legend>Способ добавления <InfoHint>XX задаёт количество пинов первого типа контакта. Другие количества можно изменить в таблице.</InfoHint></legend>
            <label><input type="radio" name="article-add-mode" value="single" checked={articleAddMode === "single"} onChange={() => setArticleAddMode("single")} />Один артикул</label>
            <label><input type="radio" name="article-add-mode" value="pattern" checked={articleAddMode === "pattern"} onChange={() => setArticleAddMode("pattern")} />По шаблону</label>
          </fieldset>
          {articleAddMode === "single" ? <label>Артикул<input value={articleKey} onChange={event => setArticleKey(event.target.value)} placeholder="B2B-XH-A" /></label>
            : <div className="series-v3-pattern-fields">
              <label>Шаблон артикула<input value={articlePattern} onChange={event => setArticlePattern(event.target.value)} placeholder="PHR-XX" /></label>
              <label>Значения XX<input value={articleVariables} onChange={event => setArticleVariables(event.target.value)} placeholder="1-14, 16, 18-20" /></label>
            </div>}
          <button type="submit" disabled={Boolean(addPreview.error) || addPreview.articles.length === 0}>+ {addPreview.articles.length > 1 ? `${addPreview.articles.length} артикулов` : "Артикул"}</button>
          {addPreview.error ? <p className="series-v3-add-error" role="alert">{addPreview.error}</p>
            : addPreview.articles.length > 0 ? <div className="series-v3-add-preview" role="status">
              <strong>Будет добавлено: {addPreview.articles.length}</strong>
              <span>{addPreview.articles.slice(0, 6).join(", ")}{addPreview.articles.length > 6 ? "…" : ""}</span>
            </div> : <p className="series-v3-add-hint">{articleAddMode === "single" ? "Введите артикул." : "Укажите шаблон с XX и значения переменной."}</p>}
        </form>
        {props.content.articleVariants.length ? <>
          <ArticleVariantsTable independentE4={props.independentE4} content={props.content} compatibleTerminals={props.compatibleTerminalArticleKeys ?? []} selectedArticleVariantId={props.selectedArticleVariantId}
            onSelect={props.onSelectArticleVariant} onUpdate={props.onUpdateArticleVariant}
            onDelete={props.onDeleteArticleVariant} onSet={props.onSetArticleContactGroup} />
          {!props.content.contactTypeGroups.length && <p>Создайте хотя бы один тип контакта, чтобы появилась колонка количества.</p>}
        </> : <p>Артикулы серии ещё не добавлены.</p>}
      </section>{props.articlePreview && <aside className="series-v3-article-preview">{props.articlePreview}</aside>}</section>
    </div>
  </section>;
}
