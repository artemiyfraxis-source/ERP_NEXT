import { useMemo, useState } from "react";
import { InfoHint } from "../InfoHint";
import { E4ConnectorInspector } from "../editor/E4ConnectorInspector";
import { createConnectorInstanceFromComponentTemplateV3 } from "../editor/component-template-placement";
import { connectorE4TableGeometry, parseConnectorSchematic, type ConnectorSchematicPresentation } from "../editor/model";
import type { TemplateAsset } from "./component-template-api";
import type { TemplateContentV5 } from "./template-model-v5";
import type { E4ConnectorSeriesTable } from "./e4-connector-series-table";
import { applyE4ConnectorRowEdit, materializeE4ConnectorArticle } from "./e4-connector-series-table";
import type { EditorCommand } from "../editor/commands";
import type { WireDatabaseOption } from "../editor/wire-database";

const labels = { number: "№", contactType: "Тип", circuit: "Цепь", terminal: "Терминал", wire: "Марка", wireSection: "Сечение", color: "Цвет" };

export function E4ArticlePreview({ content, table, articleId, assets, code, name, disabled, onChange, onTableChange, wireLookup }: {
  readonly wireLookup?: { readonly options: readonly WireDatabaseOption[]; readonly databaseOptions: readonly WireDatabaseOption[]; readonly message: string | null; readonly search: (query: string) => void };
  readonly content: TemplateContentV5;
  readonly table: E4ConnectorSeriesTable;
  readonly articleId: string | null;
  readonly assets: readonly TemplateAsset[];
  readonly code: string;
  readonly name: string;
  readonly disabled: boolean;
  readonly onChange: (value: ConnectorSchematicPresentation) => void;
  readonly onTableChange: (value: E4ConnectorSeriesTable) => void;
}) {
  const [fieldName, setFieldName] = useState("");
  const [scope, setScope] = useState<"article" | "series">("article");
  const presentation = parseConnectorSchematic(content.e4Presentation);
  const preview = useMemo(() => {
    if (!articleId) return { connector: null, error: null };
    try {
      return { connector: createConnectorInstanceFromComponentTemplateV3({
        templateId: "00000000-0000-4000-8000-000000000001", version: 1, versionSha256: "0".repeat(64),
        code: code || "Серия", name: name || "Компонент", articleBindings: content.articleVariants.map(({ sourceId, entityType, articleKey }) => ({ sourceId, entityType, articleKey })), content, assets,
      }, { legacyNumberingPreview: true, id: "library-preview", designation: "XS1", e4Position: { x: 0, y: 0 }, articleVariantId: articleId }), error: null };
    } catch (error) { return { connector: null, error: error instanceof Error ? error.message : "Не удалось построить таблицу." }; }
  }, [content, articleId, assets, code, name]);
  const authoringRows = articleId && table.articles.some(article => article.articleVariantId === articleId) ? materializeE4ConnectorArticle(table, articleId).rows : [];
  const authoringNames = Object.fromEntries(authoringRows.map(row => [`library-preview:contact:${row.seriesRowId}`, row.name]));
  const authoringNumbers = Object.fromEntries(authoringRows.map(row => [`library-preview:contact:${row.seriesRowId}`, row.number]));
  const showName = table.columns.find(column => column.id === "name")?.visible ?? true;
  const toggleName = () => onTableChange({ ...table, columns: table.columns.map(column => column.id === "name" ? { ...column, visible: !showName } : column) });
  const command = (value: EditorCommand) => {
    if (!preview.connector || disabled) return;
    if (value.type === "reset-contact-color-auto") {
      const contact = preview.connector.contacts.find(item => item.id === value.contactId);
      if (contact?.logicalContactId) onTableChange(applyE4ConnectorRowEdit(table, {
        articleVariantId: articleId!, seriesRowId: contact.logicalContactId, scope, changes: { color: "", secondaryColor: "" },
      }));
      return;
    }
    if (value.type === "update-contact") {
      const contact = preview.connector.contacts.find(item => item.id === value.contactId);
      if (!contact) return;
      const article = materializeE4ConnectorArticle(table, articleId!);
      const row = article.rows.find(item => item.seriesRowId === contact.logicalContactId);
      if (!row) return;
      const groupId = value.contactType === undefined ? undefined
        : table.contactTypeGroups.find(group => group.name === value.contactType)?.id ?? null;
      onTableChange(applyE4ConnectorRowEdit(table, { articleVariantId: articleId!, seriesRowId: row.seriesRowId, scope, changes: {
        ...(value.wire === undefined ? {} : { wire: value.wire }),
        ...(value.wireSection === undefined ? {} : { wireSection: value.wireSection }),
        ...(value.color === undefined ? {} : { color: value.color }),
        ...(value.secondaryColor === undefined ? {} : { secondaryColor: value.secondaryColor }),
        ...(value.customValues === undefined ? {} : { customValues: value.customValues }),
        ...(value.number === undefined ? {} : { number: String(value.number) }),
        ...(value.circuit === undefined ? {} : { circuitText: value.circuit || null }),
        ...(value.contactType === undefined ? {} : { contactTypeGroupId: groupId, standardTerminalArticleKey: content.terminalContactTypeBindings?.find(binding => binding.standard && binding.contactTypeGroupId === groupId)?.terminalArticleKey ?? null }),
        ...(value.terminalArticle === undefined ? {} : { standardTerminalArticleKey: value.terminalArticle
          ? row.standardTerminalArticleKey?.articleKey === value.terminalArticle ? row.standardTerminalArticleKey
            : article.contactGroups.flatMap(group => group.allowedTerminalArticleKeys).find(item => item.articleKey === value.terminalArticle) ?? null : null }),
      } }));
      return;
    }
    if (value.type === "toggle-base-column-visibility") {
      onChange({ ...presentation, baseColumns: presentation.baseColumns.map(column => column.key === value.key ? { ...column, visible: !column.visible } : column) });
      return;
    }
    if (value.type === "toggle-custom-field-visibility") {
      onChange({ ...presentation, customFields: presentation.customFields.map(field => field.id === value.fieldId ? { ...field, visible: !field.visible } : field) });
    }
  };
  return <section className="library-e4-preview" aria-label="Предпросмотр артикула Э4">
    <header><strong>На схеме Э4</strong><InfoHint>Так выбранный артикул будет выглядеть после размещения. Настройки колонок и стороны контактов применяются к серии; уже размещённые компоненты не меняются. Редактируйте строки прямо в графическом окне. Стандарт серии сохраняет ручные правки строк. Провод, цвет и дополнительные поля сохраняются как начальные значения и могут быть изменены в схеме проекта.</InfoHint></header>
    <div className="library-e4-preset">
      <label>Изменять<select aria-label="Область применения правки" value={scope} onChange={event => setScope(event.target.value as "article" | "series")}><option value="article">этот артикул</option><option value="series">всю серию</option></select></label>
      <label>Контакты<select aria-label="Сторона контактов Э4" value={presentation.orientation} disabled={disabled}
        onChange={event => onChange({ ...presentation, orientation: event.target.value as ConnectorSchematicPresentation["orientation"] })}>
        <option value="contacts-right">Справа</option><option value="contacts-left">Слева</option>
      </select></label>
      <label><input type="checkbox" checked={showName} disabled={disabled} onChange={toggleName} />Назначение</label>
      {presentation.baseColumns.map(column => <label key={column.key}><input type="checkbox" checked={column.visible} disabled={disabled}
        onChange={event => onChange({ ...presentation, baseColumns: presentation.baseColumns.map(item => item.key === column.key ? { ...item, visible: event.target.checked } : item) })} />{labels[column.key]}</label>)}
    </div>
    <div className="library-e4-preset">
      {presentation.customFields.map(field => <span key={field.id}><label><input type="checkbox" checked={field.visible} disabled={disabled}
        onChange={event => onChange({ ...presentation, customFields: presentation.customFields.map(item => item.id === field.id ? { ...item, visible: event.target.checked } : item) })} />{field.label}</label>
        <button type="button" disabled={disabled} aria-label={`Убрать поле ${field.label}`} onClick={() => onChange({ ...presentation, customFields: presentation.customFields.filter(item => item.id !== field.id) })}>×</button></span>)}
      <input aria-label="Название поля Э4" placeholder="Дополнительное поле" maxLength={120} value={fieldName} disabled={disabled} onChange={event => setFieldName(event.target.value)} />
      <button type="button" disabled={disabled || !fieldName.trim()} onClick={() => {
        onChange({ ...presentation, customFields: [...presentation.customFields, { id: crypto.randomUUID(), label: fieldName.trim(), visible: true }] }); setFieldName("");
      }}>+ Поле</button>
    </div>
    {!articleId && <span>Выберите артикул серии.</span>}
    {preview.error && <p role="alert">{preview.error}</p>}
    {preview.connector && <div className="library-e4-preview-scroll"><div style={{ width: connectorE4TableGeometry(preview.connector).width }}>
        <E4ConnectorInspector connector={preview.connector} mode="canvas" disabled={disabled} editing={!disabled} onCommand={command} onEditingChange={() => {}}
          wireOptions={wireLookup?.options} wireMaterialOptions={wireLookup?.databaseOptions} wireLookupMessage={wireLookup?.message} onWireSearch={wireLookup?.search}
          templateAuthoring={{ groups: table.contactTypeGroups, names: authoringNames, numbers: authoringNumbers, showName, onNameVisibilityChange: toggleName,
            onNumberChange: (contactId, value) => {
              const row = authoringRows.find(item => `library-preview:contact:${item.seriesRowId}` === contactId);
              if (row) onTableChange(applyE4ConnectorRowEdit(table, { articleVariantId: articleId!, seriesRowId: row.seriesRowId, scope, changes: { number: value } }));
            }, onNameChange: (contactId, value) => {
            const row = authoringRows.find(item => `library-preview:contact:${item.seriesRowId}` === contactId);
            if (row) onTableChange(applyE4ConnectorRowEdit(table, { articleVariantId: articleId!, seriesRowId: row.seriesRowId, scope, changes: { name: value } }));
          } }} />
    </div></div>}
  </section>;
}
