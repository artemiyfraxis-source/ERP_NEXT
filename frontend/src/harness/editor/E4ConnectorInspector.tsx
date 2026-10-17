import {templateContactNumberingWarning} from "./template-contact-numbering";
import { useEffect, useRef, useState } from "react";
import type { EditorCommand } from "./commands";
import {
  findConnectorSeriesArticle,
  selectConnectorSeriesArticle,
  type ConnectorSeries,
} from "./connector-series";
import {
  connectorBaseColumnKeys,
  connectorContactName,
  connectorE4Contacts,
  connectorE4TableGeometry,
  type ConnectorBaseColumnKey,
  type ConnectorContact,
  type ConnectorInstance,
  templateNameColumnId,
  templateTerminalChoices,
} from "./model";
import {
  builtInWireColors,
  type WireColorReference,
} from "./wire-reference-catalog";
import "./e4-connector-inspector.css";
import { terminalArticleLabel } from "./terminal-article-label";
import { InfoHint } from "../InfoHint";
import { AnchoredPopover } from "../AnchoredPopover";
import { builtInWireOptions, filterWireOptions, normalizeWireChoice, wireMarkChoices, wireSectionChoices, type WireDatabaseOption } from "./wire-database";
import { uniqueWireMaterialOption } from "./resolve-wire-materials";

export interface E4ConnectorInspectorProps {
  readonly templateAuthoring?: {
    readonly groups: readonly { id: string; name: string }[];
    readonly names: Readonly<Record<string, string>>;
    readonly numbers: Readonly<Record<string, string>>;
    readonly showName: boolean;
    readonly onNameVisibilityChange: () => void;
    readonly onNumberChange: (contactId: string, number: string) => void;
    readonly onNameChange: (contactId: string, name: string) => void;
  };
  readonly connector: ConnectorInstance;
  readonly onRefreshTerminals?: () => void;
  readonly refreshingTerminals?: boolean;
  readonly disabled: boolean;
  readonly onCommand: (command: EditorCommand) => void;
  readonly mode?: "panel" | "canvas";
  readonly series?: ConnectorSeries;
  readonly templateArticleOptions?: readonly {
    readonly articleVariantId: string;
    readonly articleKey: string;
  }[];
  readonly onTemplateArticleSelect?: (articleVariantId: string) => void;
  readonly terminalArticles?: readonly string[];
  readonly terminalDetails?: (article: string) => ConnectorContact["terminalDetails"];
  readonly onTerminalSearch?: (query: string) => void;
  readonly wireOptions?: readonly WireDatabaseOption[];
  readonly wireMaterialOptions?: readonly WireDatabaseOption[];
  readonly wireLookupMessage?: string | null;
  readonly onWireSearch?: (query: string) => void;
  readonly wireColors?: readonly WireColorReference[];
  readonly editing?: boolean;
  readonly onEditingChange?: (editing: boolean) => void;
}

const baseColumnLabels: Readonly<Record<ConnectorBaseColumnKey, string>> = {
  number: "№",
  contactType: "Тип",
  circuit: "Цепь",
  terminal: "Терминал",
  wire: "Марка",
  wireSection: "Сечение",
  color: "Цвет",
};

function normalizedColorKey(value: string): string {
  return value.trim().toLocaleLowerCase("ru-RU").replace(/ё/g, "е");
}

function colorHex(value: string, choices: readonly WireColorReference[]): string {
  if (/^#[0-9a-f]{6}$/i.test(value.trim())) return value.trim();
  return choices.find((choice) => normalizedColorKey(choice.name) === normalizedColorKey(value))?.hex ?? "#D9E2E7";
}

export function wireColorSwatchBackground(
  primary: string,
  secondary: string,
  choices: readonly WireColorReference[],
): string {
  const primaryHex = colorHex(primary, choices);
  return secondary
    ? `linear-gradient(225deg, ${primaryHex} 0 49%, #8da0aa 49% 51%, ${colorHex(secondary, choices)} 51% 100%)`
    : primaryHex;
}

export function updateWireQueryState(
  current: Readonly<Record<string, string>>,
  contactId: string,
  query: string | null,
): Readonly<Record<string, string>> {
  if (query !== null) return { ...current, [contactId]: query };
  if (current[contactId] === undefined) return current;
  const next = { ...current };
  delete next[contactId];
  return next;
}

function connectorColorChoices(connector: ConnectorInstance): readonly WireColorReference[] {
  const known = new Set(builtInWireColors.map((color) => normalizedColorKey(color.name)));
  const customValues = connector.contacts.flatMap((contact) => [contact.color, contact.secondaryColor ?? ""])
    .filter((value) => value.trim() !== "" && !known.has(normalizedColorKey(value)));
  return [
    ...builtInWireColors,
    ...[...new Set(customValues)].map((value) => ({
      id: `custom:${normalizedColorKey(value)}`,
      name: value,
      hex: /^#[0-9a-f]{6}$/i.test(value) ? value.toUpperCase() : "#D9E2E7",
      kind: "custom" as const,
    })),
  ];
}

function ColorCellEditor({
  contact,
  choices,
  disabled,
  editing,
  open,
  onOpenChange,
  onChange,
  onResetAuto,
}: {
  readonly contact: ConnectorContact;
  readonly choices: readonly WireColorReference[];
  readonly disabled: boolean;
  readonly editing: boolean;
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  readonly onChange: (patch: Pick<ConnectorContact, "color" | "secondaryColor">) => void;
  readonly onResetAuto: () => void;
}) {
  const primary = contact.color;
  const secondary = contact.secondaryColor ?? "";
  const swatchStyle = {
    background: wireColorSwatchBackground(primary, secondary, choices),
  };
  if (!editing) {
    return <span className="e4cce-color-readonly" title={[primary, secondary].filter(Boolean).join(" / ")}>
      <i className="e4cce-color-swatch" style={swatchStyle} aria-hidden="true" />
      <span>{[primary, secondary].filter(Boolean).join(" / ") || " "}</span>
    </span>;
  }
  return (
    <div className={`e4cce-color-editor ${open ? "is-open" : ""}`}>
      <button
        type="button"
        className="e4cce-color-summary"
        aria-label={`Цвет, контакт ${contact.number}`}
        aria-expanded={open}
        title="Основной и второй цвет провода"
        onClick={() => onOpenChange(!open)}
      >
        <i className="e4cce-color-swatch" style={swatchStyle} aria-hidden="true" />
        <span>{[primary, secondary].filter(Boolean).join(" / ") || "—"}</span>
      </button>
      <AnchoredPopover className="e4cce-color-popover" open={open} onClose={() => onOpenChange(false)} label={`Цвета провода, контакт ${contact.number}`}>
        <label>Основной цвет
          <select
            aria-label={`Основной цвет, контакт ${contact.number}`}
            value={choices.find(choice => normalizedColorKey(choice.name) === normalizedColorKey(primary))?.name ?? primary}
            disabled={disabled}
            onChange={(event) => {
              onChange({ color: event.target.value, secondaryColor: secondary });
              onOpenChange(false);
            }}
          >
            <option value="">—</option>
            {choices.map((choice) => <option key={choice.id} value={choice.name}>{choice.name}</option>)}
          </select>
        </label>
        <label>Второй цвет
          <select
            aria-label={`Второй цвет, контакт ${contact.number}`}
            value={choices.find(choice => normalizedColorKey(choice.name) === normalizedColorKey(secondary))?.name ?? secondary}
            disabled={disabled}
            onChange={(event) => {
              onChange({ color: primary, secondaryColor: event.target.value });
              onOpenChange(false);
            }}
          >
            <option value="">Пусто · одноцветный</option>
            {choices.map((choice) => <option key={choice.id} value={choice.name}>{choice.name}</option>)}
          </select>
        </label>
        <label className="e4cce-palette"><span>Новый цвет</span>
          <span className="e4cce-palette-control">
            <i className="e4cce-color-swatch" style={{ background: colorHex(primary, choices) }} aria-hidden="true" />
            <input
              type="color"
              aria-label={`Новый цвет из палитры, контакт ${contact.number}`}
              disabled={disabled}
              value={colorHex(primary, choices)}
              onChange={(event) => {
                onChange({ color: event.currentTarget.value.toUpperCase(), secondaryColor: secondary });
                onOpenChange(false);
              }}
            />
          </span>
        </label>
        <button
          type="button"
          className="e4cce-color-auto"
          disabled={disabled || contact.colorMode === "auto" ||
            contact.colorMode === undefined && !primary.trim() && !secondary.trim()}
          onClick={() => {
            onResetAuto();
            onOpenChange(false);
          }}
        >Автоматический цвет</button>
      </AnchoredPopover>
    </div>
  );
}

function nextCustomFieldId(connector: ConnectorInstance, label: string): string {
  const slug = label
    .normalize("NFC")
    .toLocaleLowerCase("ru-RU")
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-|-$/g, "") || "поле";
  const prefix = `custom-${slug}`;
  const used = new Set(connector.schematic.customFields.map((field) => field.id));
  if (!used.has(prefix)) return prefix;
  let suffix = 2;
  while (used.has(`${prefix}-${suffix}`)) suffix += 1;
  return `${prefix}-${suffix}`;
}

function nextContactNumber(connector: ConnectorInstance): number | null {
  const used = new Set(connector.contacts.map((contact) => contact.number));
  for (let number = 1; number <= 300; number += 1) if (!used.has(number)) return number;
  return null;
}

function nextContactId(connector: ConnectorInstance, number: number): string {
  const prefix = `${connector.id}:contact:${number}`;
  const used = new Set(connector.contacts.map((contact) => contact.id));
  if (!used.has(prefix)) return prefix;
  let suffix = 2;
  while (used.has(`${prefix}:${suffix}`)) suffix += 1;
  return `${prefix}:${suffix}`;
}

function TemplateNameCell({ value, label, disabled, allowEmpty = false, onCommit }: { value: string; label: string; disabled: boolean; allowEmpty?: boolean; onCommit: (value: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return <input aria-label={label} value={draft} disabled={disabled} onChange={event => setDraft(event.target.value)}
    onBlur={() => { if (allowEmpty || draft.trim()) { if (draft.trim() !== value) onCommit(draft.trim()); } else setDraft(value); }}
    onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); }} />;
}

/** Contact values live on the E4 object itself. The side panel only edits
 * identity and the set of visible fields. */
export function E4ConnectorInspector({
  templateAuthoring,
  connector,
  onRefreshTerminals,
  refreshingTerminals = false,
  disabled,
  onCommand,
  mode = "panel",
  series,
  templateArticleOptions = [],
  onTemplateArticleSelect,
  terminalArticles = [],
  terminalDetails,
  onTerminalSearch,
  wireOptions = builtInWireOptions,
  wireMaterialOptions,
  wireLookupMessage,
  onWireSearch,
  wireColors,
  editing,
  onEditingChange,
}: E4ConnectorInspectorProps) {
  const [designation, setDesignation] = useState(connector.designation);
  const [libraryCode, setLibraryCode] = useState(connector.libraryCode ?? "FREE");
  const [partNumber, setPartNumber] = useState(connector.partNumber);
  const [newFieldLabel, setNewFieldLabel] = useState("");
  const [wireQueries, setWireQueries] = useState<Readonly<Record<string, string>>>({});
  const [openColorContactId, setOpenColorContactId] = useState<string | null>(null);
  const canvasEditorRef = useRef<HTMLElement | null>(null);
  const cancelIdentityBlurRef = useRef(false);
  const canvasEditing = mode !== "canvas" || editing === true;

  useEffect(() => setDesignation(connector.designation), [connector.id, connector.designation]);
  useEffect(() => setLibraryCode(connector.libraryCode ?? "FREE"), [connector.id, connector.libraryCode]);
  useEffect(() => setPartNumber(connector.partNumber), [connector.id, connector.partNumber]);

  const commitIdentity = () => {
    if (cancelIdentityBlurRef.current) {
      cancelIdentityBlurRef.current = false;
      return;
    }
    const nextDesignation = designation.trim();
    const hasLibraryIdentity = connector.libraryBinding?.mode === "series" ||
      connector.libraryBinding?.mode === "template";
    const nextLibraryCode = hasLibraryIdentity
      ? connector.libraryCode ?? "FREE"
      : libraryCode.trim();
    const nextPartNumber = hasLibraryIdentity ? connector.partNumber : partNumber.trim();
    if (!nextDesignation || !nextLibraryCode || !nextPartNumber) {
      setDesignation(connector.designation);
      setLibraryCode(connector.libraryCode ?? "FREE");
      setPartNumber(connector.partNumber);
      return;
    }
    if (nextDesignation !== connector.designation || nextLibraryCode !== (connector.libraryCode ?? "FREE") ||
        nextPartNumber !== connector.partNumber) {
      onCommand({
        type: "update-connector",
        connectorId: connector.id,
        designation: nextDesignation,
        libraryCode: hasLibraryIdentity ? undefined : nextLibraryCode,
        partNumber: hasLibraryIdentity ? undefined : nextPartNumber,
      });
    }
  };

  const selectArticle = (nextPartNumber: string) => {
    if (!series || nextPartNumber === connector.partNumber) return;
    const selected = selectConnectorSeriesArticle(connector, series, nextPartNumber).connector;
    onCommand({
      type: "apply-connector-article",
      connectorId: connector.id,
      partNumber: selected.partNumber,
      contacts: selected.contacts,
      libraryBinding: selected.libraryBinding,
    });
  };

  const addCustomField = () => {
    const label = newFieldLabel.trim();
    if (!label) return;
    onCommand({
      type: "add-custom-field",
      connectorId: connector.id,
      field: { id: nextCustomFieldId(connector, label), label, visible: true },
    });
    setNewFieldLabel("");
  };

  const updateContact = (contact: ConnectorInstance["contacts"][number], patch: Omit<Partial<typeof contact>, "wireDiameterMm" | "materialBinding"> & { wireDiameterMm?: number | null; materialBinding?: ConnectorContact["materialBinding"] | null }) => {
    onCommand({
      type: "update-contact",
      connectorId: connector.id,
      contactId: contact.id,
      number: patch.number,
      nameOverride: patch.nameOverride,
      contactType: patch.contactType,
      circuit: patch.circuit,
      terminalArticle: patch.terminalArticle,
      terminalDetails: patch.terminalArticle === undefined ? undefined : terminalDetails?.(patch.terminalArticle),
      wire: patch.wire,
      wireSection: patch.wireSection,
      wireDiameterMm: patch.wireDiameterMm,
      materialBinding: patch.materialBinding,
      color: patch.color,
      secondaryColor: patch.secondaryColor,
      connectionStatus: patch.connectionStatus,
      customValues: patch.customValues,
    });
  };

  const addContact = () => {
    const number = nextContactNumber(connector);
    if (number === null) return;
    onCommand({
      type: "add-contact",
      connectorId: connector.id,
      contact: {
        id: nextContactId(connector, number), number, contactType: "", circuit: "",
        terminalArticle: "", wire: "", color: "", secondaryColor: "", connectionStatus: "available", customValues: {},
      },
    });
  };

  if (mode === "canvas") {
    const isSeries = connector.libraryBinding?.mode === "series";
    const isTemplate = connector.libraryBinding?.mode === "template";
    const isLibrary = isSeries || isTemplate;
    const article = isSeries && series ? findConnectorSeriesArticle(series, connector.partNumber) : null;
    const displayedLibraryCode = isSeries
      ? series?.name ?? connector.libraryBinding?.seriesId ?? "СЕРИЯ"
      : isTemplate ? connector.libraryBinding.snapshot.code : connector.libraryCode ?? "FREE";
    const colorChoices = wireColors ?? connectorColorChoices(connector);
    const geometry = connectorE4TableGeometry(connector);
    const rows = connectorE4Contacts(connector);
    const columns = geometry.columns.map((column) => column.kind === "base"
      ? { id: column.key, label: baseColumnLabels[column.key], width: column.width }
      : { id: `custom:${column.id}` as const, label: column.label, width: column.width });
    if (templateAuthoring && !templateAuthoring.showName) {
      const index = columns.findIndex(column => column.id === `custom:${templateNameColumnId}`);
      if (index >= 0) columns.splice(index, 1);
    }
    return (
      <section
          ref={canvasEditorRef}
          className={`e4-connector-canvas-editor ${connector.schematic.orientation} ${canvasEditing ? "is-editing" : "is-readonly"}`}
          aria-label={`Поля соединителя ${connector.designation}`}
          title={canvasEditing ? "Редактирование включено. Escape — закончить" : "Зажмите и перетащите. Двойной клик — редактировать"}
          onKeyDown={(event) => {
            if (event.key === "Escape" && canvasEditing) {
              event.stopPropagation();
              onEditingChange?.(false);
            }
          }}
        >
        {!templateAuthoring && templateContactNumberingWarning(connector) && <InfoHint>{templateContactNumberingWarning(connector)}</InfoHint>}
        <datalist id={`terminal-articles-${connector.id}`}>
          {terminalArticles.map((terminal) => <option key={terminal} value={terminal} />)}
        </datalist>
        <div
          className="e4cce-title"
          title={canvasEditing ? "Редактирование" : "Перетащить соединитель"}
        >
          {onRefreshTerminals && <button type="button" className="e4cce-refresh" disabled={disabled || refreshingTerminals}
            aria-label="Обновить терминалы серии" onClick={onRefreshTerminals}>↻</button>}
          <button
            type="button"
            className="e4cce-title-add"
            disabled={disabled || isLibrary || nextContactNumber(connector) === null}
            title={isLibrary ? "Число строк задаётся выбранным библиотечным артикулом" : "Добавить строку контакта"}
            onClick={addContact}
          >⊕ {isLibrary ? "Строки из артикула" : "Добавить строку"}</button>
          {canvasEditing ? (
            <input
              type="text"
              value={designation}
              disabled={disabled || Boolean(templateAuthoring)}
              aria-label="Обозначение соединителя"
              title="Обозначение соединителя"
              onChange={(event) => setDesignation(event.target.value)}
              onBlur={commitIdentity}
              onKeyDown={(event) => {
                if (event.key === "Enter") event.currentTarget.blur();
                if (event.key === "Escape") {
                  event.stopPropagation();
                  setDesignation(connector.designation);
                  onEditingChange?.(false);
                }
              }}
            />
          ) : <strong title={connector.designation}>{connector.designation}</strong>}
        </div>
        <div className="e4cce-table-scroll">
          <table>
            <colgroup>{columns.length === 0
              ? <col style={{ width: geometry.width }} />
              : columns.map((column) => <col key={column.id} style={{ width: column.width }} />)}</colgroup>
            <thead><tr>
              {columns.length === 0 ? <th className="e4cce-empty-column">Нет видимых полей</th> : columns.map((column) => (
                <th key={column.id} title="Изменить видимость поля">
                  <span>{column.label}</span>
                  <button
                    type="button"
                    aria-label={`Скрыть поле ${column.label}`}
                    disabled={disabled || !canvasEditing}
                    onClick={() => column.id === `custom:${templateNameColumnId}`
                      ? templateAuthoring ? templateAuthoring.onNameVisibilityChange()
                        : onCommand({ type: "set-name-column-visibility", connectorId: connector.id, visible: false })
                      : column.id.startsWith("custom:")
                      ? onCommand({ type: "toggle-custom-field-visibility", connectorId: connector.id, fieldId: column.id.slice(7) })
                      : onCommand({ type: "toggle-base-column-visibility", connectorId: connector.id, key: column.id as ConnectorBaseColumnKey })}
                  >◉</button>
                </th>
              ))}
            </tr></thead>
            <tbody>{rows.map((contact, rowIndex) => (
              <tr key={contact.id}>
                {columns.length === 0 ? <td className="e4cce-empty-column">&nbsp;</td> : columns.map((column) => {
                  const templateContact = isTemplate
                    ? connector.libraryBinding.snapshot.contacts.find((candidate) =>
                      candidate.logicalContactId === contact.logicalContactId)
                    : undefined;
                  const value = column.id === "number" ? String(contact.number)
                    : column.id === "contactType" ? contact.contactType
                      : column.id === "circuit" ? contact.circuit
                        : column.id === "terminal" ? contact.terminalArticle
                          : column.id === "wire" ? contact.wire
                            : column.id === "wireSection" ? contact.wireSection ?? ""
                            : column.id === "color" ? contact.color
                              : column.id === `custom:${templateNameColumnId}` ? connectorContactName(connector, contact)
                              : contact.customValues[column.id.slice(7)] ?? "";
                  const terminalOptions = contact.libraryContact && article
                    ? article.allowedTerminalArticles[contact.libraryContact.kind]
                    : isTemplate
                      ? templateTerminalChoices(connector, contact.logicalContactId)
                      : templateContact?.allowedTerminalArticleKeys.map((candidate) => candidate.articleKey) ?? [];
                  const lockedByLibrary = !templateAuthoring && isLibrary && (column.id === "number" || column.id === "contactType");
                  const cellDisabled = disabled;
                  const lockedByLibraryTitle = isTemplate
                    ? "Номер и тип заданы закреплённым шаблоном"
                    : "Номер и тип заданы артикулом серии";
                  const materialChoices = wireMaterialOptions?.length ? wireMaterialOptions : wireOptions;
                  const sectionChoices = wireSectionChoices(materialChoices, contact.wire, contact.wireSection ?? "");
                  const input = templateAuthoring && column.id === "number" ? <TemplateNameCell
                    label={`Номер, контакт ${contact.number}`} value={templateAuthoring.numbers[contact.id] ?? ""} disabled={disabled}
                    onCommit={value => templateAuthoring.onNumberChange(contact.id, value)} />
                    : column.id === `custom:${templateNameColumnId}` ? <TemplateNameCell
                    allowEmpty={!templateAuthoring} label={`Назначение, контакт ${contact.number}`} value={templateAuthoring ? templateAuthoring.names[contact.id] ?? "" : value} disabled={disabled || !canvasEditing}
                    onCommit={name => templateAuthoring ? templateAuthoring.onNameChange(contact.id, name) : updateContact(contact, { nameOverride: name })} />
                    : templateAuthoring && column.id === "contactType" ? <select aria-label={`Тип, контакт ${contact.number}`} value={contact.contactType} disabled={disabled}
                      onChange={event => updateContact(contact, { contactType: event.target.value })}>
                      <option value="">Не назначен</option>{templateAuthoring.groups.map(group => <option key={group.id} value={group.name}>{group.name}</option>)}
                    </select> : column.id === "color" ? (
                    <ColorCellEditor
                      contact={contact}
                      choices={colorChoices}
                      disabled={cellDisabled}
                      editing={canvasEditing}
                      open={openColorContactId === contact.id}
                      onOpenChange={(open) => setOpenColorContactId(open ? contact.id : null)}
                      onChange={(patch) => updateContact(contact, patch)}
                      onResetAuto={() => onCommand({
                        type: "reset-contact-color-auto",
                        connectorId: connector.id,
                        contactId: contact.id,
                      })}
                    />
                  ) : column.id === "terminal" && isLibrary ? (
                    <select
                      value={value}
                      disabled={cellDisabled || !canvasEditing}
                      aria-label={`${column.label}, контакт ${contact.number}`}
                      title="Допустимые терминалы для этого типа контакта"
                      onChange={(event) => updateContact(contact, { terminalArticle: event.target.value })}
                    >
                      <option value="">—</option>
                      {isSeries && !terminalOptions.includes(value) && value && <option value={value}>{terminalArticleLabel(value)}</option>}
                      {terminalOptions.map((terminal) => <option key={terminal} value={terminal}>{terminalArticleLabel(terminal)}</option>)}
                    </select>
                  ) : column.id === "wireSection" ? <select
                    aria-label={`Сечение, контакт ${contact.number}`} value={value} disabled={cellDisabled || !canvasEditing}
                    title={contact.wire.trim() ? "Сечения выбранной марки провода" : "Выберите сечение, чтобы отфильтровать марки проводов"}
                    onChange={event => {
                      const section = event.target.value;
                      const option = uniqueWireMaterialOption({ ...contact, wireSection: section }, materialChoices)
                        ?? materialChoices.find(item => !item.materialBinding && normalizeWireChoice(item.mark) === normalizeWireChoice(contact.wire) && normalizeWireChoice(item.section) === normalizeWireChoice(section));
                      updateContact(contact, { wireSection: section, wireDiameterMm: option?.diameterMm ?? null, materialBinding: option?.materialBinding ?? null,
                        ...(option?.color ? { color: option.color } : {}) });
                    }}
                  ><option value="">—</option>{sectionChoices.map(section => <option key={section} value={section}>{section}</option>)}</select>
                  : column.id === "wire" ? <div className="e4cce-wire-picker">
                    <input
                      type="text"
                      value={value}
                      disabled={cellDisabled || !canvasEditing}
                      aria-label={`${column.label}, контакт ${contact.number}`}
                      autoComplete="off"
                      onChange={(event) => {
                        const query = event.currentTarget.value;
                        // Typing a mark keeps a section chosen first. The list below
                        // offers only rows that contain this exact pair.
                        updateContact(contact, { wire: query, wireSection: contact.wireSection ?? "", wireDiameterMm: null, materialBinding: null });
                        setWireQueries((current) => updateWireQueryState(current, contact.id, query));
                        onWireSearch?.(query);
                      }}
                      onFocus={(event) => {
                        // React clears currentTarget after the handler. Capture
                        // the value before scheduling the functional update.
                        const query = event.currentTarget.value;
                        setWireQueries((current) => updateWireQueryState(current, contact.id, query));
                        onWireSearch?.(query);
                      }}
                    />
                    {wireQueries[contact.id] !== undefined && <AnchoredPopover className="e4cce-wire-suggestions" role="listbox" label={`Подсказки проводов, контакт ${contact.number}`} open onClose={() => setWireQueries(current => updateWireQueryState(current, contact.id, null))}>
                      {wireLookupMessage && <small role="status">{wireLookupMessage}</small>}
                      {wireMarkChoices(materialChoices, wireQueries[contact.id] ?? "", contact.wireSection ?? "")
                        .map((mark) => <button
                          type="button"
                          key={mark}
                          onPointerDown={(event) => event.preventDefault()}
                          onClick={() => {
                            const option = uniqueWireMaterialOption({ ...contact, wire: mark }, materialChoices);
                            updateContact(contact, { wire: mark, wireSection: contact.wireSection ?? "", wireDiameterMm:option?.diameterMm ?? null,
                              materialBinding: option?.materialBinding ?? null });
                            setWireQueries((current) => updateWireQueryState(current, contact.id, null));
                          }}
                        >{mark}</button>)}
                    </AnchoredPopover>}
                  </div> : <input
                    type={column.id === "number" ? "number" : "text"}
                    min={column.id === "number" ? 1 : undefined}
                    max={column.id === "number" ? 300 : undefined}
                    value={value}
                    list={column.id === "terminal" && !isLibrary ? `terminal-articles-${connector.id}` : undefined}
                    disabled={cellDisabled || !canvasEditing || lockedByLibrary}
                    title={lockedByLibrary ? lockedByLibraryTitle : undefined}
                    aria-label={`${column.label}, контакт ${contact.number}`}
                    onChange={(event) => {
                      if (column.id === "number") {
                        const number = Number(event.target.value);
                        if (Number.isSafeInteger(number) && number >= 1 && number <= 300) updateContact(contact, { number });
                      } else if (column.id.startsWith("custom:")) {
                        updateContact(contact, { customValues: { ...contact.customValues, [column.id.slice(7)]: event.target.value } });
                      } else {
                        const patch = column.id === "contactType" ? { contactType: event.target.value }
                        : column.id === "circuit" ? { circuit: event.target.value }
                            : column.id === "terminal" ? { terminalArticle: event.target.value }
                              : column.id === "wire" ? { wire: event.target.value }
                                : column.id === "wireSection" ? { wireSection: event.target.value }
                                : { color: event.target.value };
                        updateContact(contact, patch);
                        if (column.id === "terminal") onTerminalSearch?.(event.target.value);
                      }
                    }}
                  />;
                  const cell = column.id === "color" ? input
                    : canvasEditing ? input : <span className="e4cce-readonly-value">{(column.id === "terminal" ? terminalArticleLabel(value) : value) || " "}</span>;
                  return <td key={column.id} className={column.id === "number" && !templateAuthoring ? "e4cce-number" : undefined}>{cell}{column.id === "number" && canvasEditing && !templateAuthoring && <span className="e4cce-row-actions">
                    <button type="button" disabled={disabled || rowIndex === 0}
                      aria-label={`Переместить контакт ${contact.number} вверх`} title="Строку вверх"
                      onClick={() => onCommand({ type: "move-contact-row", connectorId: connector.id, contactId: contact.id, direction: -1 })}>↑</button>
                    <button type="button" disabled={disabled || rowIndex === rows.length - 1}
                      aria-label={`Переместить контакт ${contact.number} вниз`} title="Строку вниз"
                      onClick={() => onCommand({ type: "move-contact-row", connectorId: connector.id, contactId: contact.id, direction: 1 })}>↓</button>
                    <button
                      type="button"
                      className={contact.connectionStatus === "not-connected" ? "active" : ""}
                      disabled={disabled || Boolean(templateAuthoring)}
                      aria-label={`Контакт ${contact.number} не подключён`}
                      aria-pressed={contact.connectionStatus === "not-connected"}
                      title={contact.connectionStatus === "not-connected" ? "Снять отметку «не подключено»" : "Пометить как не подключено"}
                      onClick={() => updateContact(contact, { connectionStatus: contact.connectionStatus === "not-connected" ? "available" : "not-connected" })}
                    >×</button>
                    <button
                      type="button"
                      disabled={disabled || isLibrary}
                      aria-label={`Удалить контакт ${contact.number}`}
                      title={`Удалить контакт ${contact.number}`}
                      onClick={() => onCommand({ type: "remove-contact", connectorId: connector.id, contactId: contact.id })}
                    >−</button>
                  </span>}</td>;
                })}
              </tr>
            ))}</tbody>
            <tfoot><tr><td colSpan={Math.max(1, columns.length)}>
              <div className="e4cce-footer">
                {canvasEditing && !isLibrary ? <input
                  className="e4cce-footer-code"
                  aria-label="Код свободного блока"
                  maxLength={120}
                  disabled={disabled}
                  value={libraryCode}
                  onChange={(event) => setLibraryCode(event.target.value)}
                  onBlur={commitIdentity}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") event.currentTarget.blur();
                    if (event.key === "Escape") {
                      event.stopPropagation();
                      setLibraryCode(connector.libraryCode ?? "FREE");
                    }
                  }}
                /> : <span className="e4cce-footer-code" title={displayedLibraryCode}>{displayedLibraryCode}</span>}
                {canvasEditing && !isLibrary ? <input
                  className="e4cce-footer-article"
                  aria-label="Артикул свободного блока"
                  maxLength={512}
                  disabled={disabled}
                  value={partNumber}
                  onChange={(event) => setPartNumber(event.target.value)}
                  onBlur={commitIdentity}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") event.currentTarget.blur();
                    if (event.key === "Escape") {
                      event.stopPropagation();
                      setPartNumber(connector.partNumber);
                    }
                  }}
                /> : <span className="e4cce-footer-article" title={connector.partNumber}>{connector.partNumber}</span>}
              </div>
            </td></tr></tfoot>
          </table>
        </div>
      </section>
    );
  }

  return (
    <section className="e4-connector-inspector" aria-label={`Соединитель ${connector.designation}`}>
      {templateContactNumberingWarning(connector) && <p role="alert">{templateContactNumberingWarning(connector)}</p>}
      <header className="e4ci-header">
        <div><span className="e4ci-eyebrow">Схема Э4 · соединитель</span><strong>{connector.designation}</strong></div>
        <button
          type="button"
          className="e4ci-flip-button"
          disabled={disabled}
          onClick={() => onCommand({ type: "flip-connector-orientation", connectorId: connector.id })}
          title="Перенести контакты на другую сторону"
        >⇆ Зеркальный вид</button>
      </header>

      <div className="e4ci-identity">
        {onRefreshTerminals && <div className="e4ci-refresh-row">
          <button className="ui-control" type="button" disabled={disabled || refreshingTerminals} onClick={onRefreshTerminals}>Обновить терминалы</button>
          <InfoHint>Загружает совместимые терминалы опубликованной серии. Закреплённая версия компонента, проводка и ручные значения сохраняются.</InfoHint>
        </div>}
        <label>Обозначение
          <input
            value={designation}
            maxLength={120}
            disabled={disabled}
            onChange={(event) => setDesignation(event.target.value)}
            onBlur={commitIdentity}
            onKeyDown={(event) => {
              if (event.key === "Enter") { event.preventDefault(); commitIdentity(); event.currentTarget.blur(); }
              if (event.key === "Escape") {
                cancelIdentityBlurRef.current = true;
                setDesignation(connector.designation);
                event.currentTarget.blur();
              }
            }}
          />
        </label>
        <label>{connector.libraryBinding?.mode === "series" ? "Артикул серии" :
          connector.libraryBinding?.mode === "template" ? "Артикул шаблона" : "Артикул"}
          {connector.libraryBinding?.mode === "series" && series ? (
            <select value={connector.partNumber} disabled={disabled} onChange={(event) => selectArticle(event.target.value)}>
              {series.articles.map((article) => (
                <option key={article.partNumber} value={article.partNumber}>{article.partNumber}</option>
              ))}
            </select>
          ) : connector.libraryBinding?.mode === "template" && templateArticleOptions.length > 0 && onTemplateArticleSelect ? (
            <select
              aria-label="Артикул шаблона"
              value={connector.libraryBinding.articleVariantId}
              disabled={disabled}
              onChange={(event) => onTemplateArticleSelect(event.currentTarget.value)}
            >
              {templateArticleOptions.map((article) => (
                <option key={article.articleVariantId} value={article.articleVariantId}>{article.articleKey}</option>
              ))}
            </select>
          ) : connector.libraryBinding?.mode === "template" ? (
            <span className="e4ci-readonly-value" title="Артикул закреплён выбранным вариантом шаблона">
              {connector.partNumber}
            </span>
          ) : (
            <input
              value={partNumber}
              maxLength={512}
              disabled={disabled}
              onChange={(event) => setPartNumber(event.target.value)}
              onBlur={commitIdentity}
              onKeyDown={(event) => {
                if (event.key === "Enter") { event.preventDefault(); commitIdentity(); event.currentTarget.blur(); }
                if (event.key === "Escape") {
                  cancelIdentityBlurRef.current = true;
                  setPartNumber(connector.partNumber);
                  event.currentTarget.blur();
                }
              }}
            />
          )}
        </label>
        <div className="e4ci-identity-help"><span>Артикул и контакты</span><InfoHint>{connector.libraryBinding?.mode === "series"
          ? "Артикул определяет число и типы контактов. Значения цепей сохраняются для совпавших строк."
          : connector.libraryBinding?.mode === "template"
            ? templateArticleOptions.length > 0 && onTemplateArticleSelect
              ? "Артикул выбирается из семейства. Код, число и типы контактов обновляются по выбранному варианту."
              : "Артикул, код, число и типы контактов закреплены версией шаблона. Цепи, провода, цвета и совместимые терминалы можно редактировать."
            : "Свободный экземпляр: строки и поля можно менять независимо от библиотеки."}</InfoHint></div>
      </div>

      <div className="e4ci-canvas-edit-note">
        <strong>Редактирование таблицы</strong>
        <InfoHint>Двойной клик по таблице включает редактирование. Стрелки рядом с номером перемещают строку целиком только в Э4; значения, номера контактов и соединения сохраняются. Ctrl+Z отменяет перестановку. Кнопки глаза в заголовках скрывают поле; вернуть его можно ниже.</InfoHint>
      </div>

      <details className="e4ci-column-settings" open>
        <summary>Поля таблицы · скрыть / показать</summary>
        <div className="e4ci-base-columns">
          {connector.libraryBinding?.mode === "template" && <>
            <button type="button" className={connector.schematic.showName !== false ? "e4ci-column-toggle active" : "e4ci-column-toggle"}
              disabled={disabled} aria-pressed={connector.schematic.showName !== false}
              title={connector.schematic.showName !== false ? "Скрыть поле «Назначение»" : "Показать поле «Назначение»"}
              onClick={() => onCommand({ type: "set-name-column-visibility", connectorId: connector.id, visible: connector.schematic.showName === false })}>
              <span aria-hidden="true">{connector.schematic.showName !== false ? "◉" : "○"}</span><span>Назначение</span>
              <small>{connector.schematic.showName !== false ? "видимо" : "скрыто"}</small>
            </button>
            <InfoHint>Назначение меняется прямо в таблице. Скрытие сохраняет значения; общая настройка действует на все размещённые библиотечные компоненты этой схемы. Для новых размещений задайте видимость в библиотеке серии.</InfoHint>
            <div className="e4ci-name-global">
              <button type="button" disabled={disabled} aria-label="Скрыть назначение во всей схеме" onClick={() => onCommand({ type: "set-name-column-visibility", connectorId: connector.id, visible: false, scope: "document" })}>Скрыть у всех</button>
              <button type="button" disabled={disabled} aria-label="Показать во всей схеме" onClick={() => onCommand({ type: "set-name-column-visibility", connectorId: connector.id, visible: true, scope: "document" })}>Показать у всех</button>
            </div>
          </>}
          {connectorBaseColumnKeys.map((key) => {
            const visible = connector.schematic.baseColumns.find((item) => item.key === key)?.visible ?? true;
            return (
              <button
                type="button"
                className={visible ? "e4ci-column-toggle active" : "e4ci-column-toggle"}
                key={key}
                disabled={disabled}
                aria-pressed={visible}
                title={visible ? `Скрыть поле «${baseColumnLabels[key]}»` : `Показать поле «${baseColumnLabels[key]}»`}
                onClick={() => onCommand({ type: "toggle-base-column-visibility", connectorId: connector.id, key })}
              >
                <span aria-hidden="true">{visible ? "◉" : "○"}</span>
                <span>{baseColumnLabels[key]}</span>
                <small>{visible ? "видимо" : "скрыто"}</small>
              </button>
            );
          })}
        </div>
        <div className="e4ci-custom-columns">
          <div className="e4ci-section-title"><strong>Справочные поля</strong><InfoHint>Дополнительные текстовые колонки. Их значения заполняются в таблице схемы.</InfoHint></div>
          {connector.schematic.customFields.length === 0 && <p>Дополнительных полей пока нет.</p>}
          {connector.schematic.customFields.map((field) => (
            <div className="e4ci-custom-column" key={field.id}>
              <button
                type="button"
                className={field.visible ? "active" : ""}
                disabled={disabled}
                onClick={() => onCommand({ type: "toggle-custom-field-visibility", connectorId: connector.id, fieldId: field.id })}
                aria-pressed={field.visible}
                title={field.visible ? "Скрыть поле" : "Показать поле"}
              >{field.visible ? "◉" : "○"}</button>
              <span>{field.label}</span>
              <button
                type="button"
                className="danger"
                disabled={disabled}
                onClick={() => onCommand({ type: "remove-custom-field", connectorId: connector.id, fieldId: field.id })}
                title="Удалить справочное поле и его значения"
              >×</button>
            </div>
          ))}
          <div className="e4ci-add-field">
            <input
              value={newFieldLabel}
              maxLength={120}
              disabled={disabled}
              placeholder="Название нового поля"
              aria-label="Название нового справочного поля"
              onChange={(event) => setNewFieldLabel(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addCustomField(); } }}
            />
            <button type="button" disabled={disabled || !newFieldLabel.trim()} onClick={addCustomField}>Добавить</button>
          </div>
        </div>
      </details>
    </section>
  );
}
