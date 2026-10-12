// @vitest-environment node
import { describe, expect, it } from "vitest";
import { addArticleVariantsV3, addContactTypeGroupV3, newTemplateContentV3, setArticleVariantContactGroupV3 } from "./template-commands-v3";
import { applyE4ConnectorRowEdit, createE4ConnectorSeriesTableFromV3 } from "./e4-connector-series-table";
import { createTemplateContentV5FromEditor, projectTemplateContentV5ToV3, validateTemplateContentV5 } from "./template-model-v5";
import { createConnectorInstanceFromComponentTemplateV3 } from "../editor/component-template-placement";
import { componentPlacementRequest } from "../editor/component-placement-api";
import { parseConnectorSchematic } from "../editor/model";

describe("independent article table workflow", () => {
  it("sets 12 pins without graphic prototypes, saves and places the chosen article with its preset", () => {
    let core = newTemplateContentV3();
    let group: string;
    [core, group] = addContactTypeGroupV3(core, "Сигнал");
    core = addArticleVariantsV3(core, ["A-2", "A-12"].map(articleKey => ({ sourceId: "БД.СОЕД", entityType: "Connector", articleKey })));
    core = setArticleVariantContactGroupV3(core, core.articleVariants[0]!.id, group, 2, []);
    core = setArticleVariantContactGroupV3(core, core.articleVariants[1]!.id, group, 12, []);
    let table = createE4ConnectorSeriesTableFromV3(core, true);
    table = applyE4ConnectorRowEdit(table, {articleVariantId:core.articleVariants[1]!.id,
      seriesRowId:table.articles[1]!.rows[0]!.seriesRowId,scope:"article",
      changes:{wire:"ПВ-3",color:"Красный",secondaryColor:"Белый",customValues:{note:"Преднастройка"}}});
    expect(table.articles.map(article => article.rows.length)).toEqual([2, 12]);
    const defaults = parseConnectorSchematic(undefined);
    const preset = { ...defaults, orientation: "contacts-left" as const,
      baseColumns: defaults.baseColumns.map(column => ({ ...column, visible: column.key !== "wire" })),
      customFields: [{ id: "note", label: "Примечание", visible: true }] };
    const content = createTemplateContentV5FromEditor(core, table, [], [], preset).content;
    expect(validateTemplateContentV5(content).valid).toBe(true);
    expect(projectTemplateContentV5ToV3(content)).not.toHaveProperty("e4Presentation");
    const instance = createConnectorInstanceFromComponentTemplateV3({
      templateId: "10000000-0000-4000-8000-000000000001", version: 3, versionSha256: "a".repeat(64),
      code: "TEST", name: "Test", content, assets: [],
      articleBindings: core.articleVariants.map(({ sourceId, entityType, articleKey }) => ({ sourceId, entityType, articleKey })),
    }, { id: "placed", designation: "XS1", e4Position: { x: 0, y: 0 }, articleVariantId: core.articleVariants[1]!.id });
    expect(instance.contacts).toHaveLength(12);
    expect(instance.contacts[0]).toMatchObject({wire:"ПВ-3",color:"Красный",secondaryColor:"Белый",colorMode:"manual",customValues:{note:"Преднастройка"}});
    expect(instance.contacts[1]).toMatchObject({wire:"",color:"",customValues:{}});
    expect(instance.schematic).toEqual({ ...preset, showName: true });
    const request = componentPlacementRequest(instance, 0, "command");
    expect(request).toMatchObject({ sourceVersion: 3, sourceId: "бд.соед", entityType: "connector", articleKey: "A-12" });
    expect(request.instance.libraryBinding).toMatchObject({ versionSha256: "a".repeat(64), templateVersion: 3 });
    const reloaded = JSON.parse(JSON.stringify(content));
    expect(reloaded.e4Presentation).toEqual(preset);
    reloaded.e4Presentation.orientation = "wrong";
    expect(validateTemplateContentV5(reloaded).valid).toBe(false);
  });
});
