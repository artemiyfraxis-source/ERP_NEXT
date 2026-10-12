// @vitest-environment node
import { describe, expect, it } from "vitest";
import { applyEditorCommand, createConnector, createWire, e4RoutingIssues, normalizeE4RoutingDocument } from "./commands";
import { createEditorHistory, executeEditorCommand, redoEditorCommand, undoEditorCommand } from "./history";
import { validateE4Route } from "./e4-router";
import { designToScene } from "./HarnessDesignEditor";
import { getE4ScreenLayout, getE4DifferentialPairLayout } from "./CanvasViewport";
import { commonHorizontalPairSpan } from "./e4-parallel-spans";
import { builtInConnectorSeries, createBuiltInConnectorInstance } from "./connector-series-demo";
import { selectConnectorSeriesArticle } from "./connector-series";
import {
  connectorContactPosition,
  connectorE4Contacts,
  connectorE4TableGeometry,
  calculateWireCutLength,
  createJunctionEndpoint,
  createScreenEndpoint,
  createOrthogonalE4Route,
  createEmptyHarnessDesign,
  parseHarnessDesignDocument,
  validateOrthogonalE4Route,
  wireEndpointE4Anchor,
  wireScreenConnectionGeometry,
  wireE4PathContainsPoint,
  type ConnectorInstance,
} from "./model";

function templateConnector(): ConnectorInstance {
  const base = createConnector("template", "X1", 1, { x: 0, y: 0 }, undefined, "XH-1");
  const article = { sourceId: "БД.СОЕД", entityType: "connector", articleKey: "XH-1" };
  const contact = {
    ...base.contacts[0]!,
    id: "template:contact:logical-1",
    logicalContactId: "logical-1",
    contactType: "сигнальный",
    libraryContact: null,
  };
  return {
    ...base,
    libraryCode: "JST-XH",
    contacts: [contact],
    libraryBinding: {
      mode: "template",
      templateId: "template-1",
      templateVersion: 3,
      versionSha256: "a".repeat(64),
      articleVariantId: "variant-1",
      article,
      snapshot: {
        templateId: "template-1",
        templateVersion: 3,
        versionSha256: "a".repeat(64),
        code: "JST-XH",
        name: "JST XH",
        articleVariantId: "variant-1",
        article,
        articleBindings: [article],
        assets: [],
        contacts: [{
          logicalContactId: "logical-1",
          prototypeLogicalContactId: "prototype-1",
          sourceNumber: "1",
          name: "Сигнал",
          circuitText: null,
          contactTypeGroupId: "signal-group",
          contactType: "сигнальный",
          allowedTerminalArticleKeys: [
            { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "T-1" },
            { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "T-2" },
          ],
          representations: [],
        }],
      },
    },
  };
}

describe("shared harness editor model", () => {
  it("reorders E4 rows without altering contacts, drawing anchors or wire identity; undo restores routes", () => {
    const before = connectionDocument();
    const connector = before.connectors[0]!;
    const history = executeEditorCommand(createEditorHistory(before), {
      type: "move-contact-row", connectorId: connector.id, contactId: connector.contacts[0]!.id, direction: 1,
    });
    const after = history.present;
    const moved = after.connectors[0]!;
    expect(moved.contacts).toEqual(connector.contacts);
    expect(connectorE4Contacts(moved).map(c => c.number)).toEqual([2, 1, 3, 4]);
    expect(after.wires.map(w => [w.id, w.from, w.to, w.lengthMm, w.drawingRoute])).toEqual(before.wires.map(w => [w.id, w.from, w.to, w.lengthMm, w.drawingRoute]));
    for (const contact of connector.contacts) {
      expect(connectorContactPosition(moved, contact.id, "drawing")).toEqual(connectorContactPosition(connector, contact.id, "drawing"));
    }
    expect(connectorContactPosition(moved, connector.contacts[0]!.id, "e4")!.y).toBe(connectorContactPosition(connector, connector.contacts[1]!.id, "e4")!.y);
    expect(after.wires[0]!.e4Route).not.toEqual(before.wires[0]!.e4Route);
    for (const wire of after.wires) {
      expect(() => validateOrthogonalE4Route(wireEndpointE4Anchor(after, wire.from)!, wire.e4Route, wireEndpointE4Anchor(after, wire.to)!)).not.toThrow();
    }
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(after))).connectors[0]!.schematic.rowOrder).toEqual(moved.schematic.rowOrder);
    expect(undoEditorCommand(history).present).toEqual(before);
    expect(redoEditorCommand(undoEditorCommand(history)).present).toEqual(after);
    const unchanged = applyEditorCommand(after, { type: "move-contact-row", connectorId: moved.id, contactId: moved.contacts[1]!.id, direction: -1 });
    expect(unchanged).toEqual(after);
    const resolved = applyEditorCommand(after, { type: "move-contact-row", connectorId: moved.id, contactId: moved.contacts[0]!.id, direction: -1 });
    expect(e4RoutingIssues(resolved)).toEqual([]);
  });

  it("drops retired row IDs and rejects duplicate or unknown persisted rows", () => {
    let document: ReturnType<typeof createEmptyHarnessDesign> = { ...createEmptyHarnessDesign(), connectors: [createConnector("x", "X", 3, { x: 0, y: 0 })] };
    document = applyEditorCommand(document, { type: "move-contact-row", connectorId: "x", contactId: "x:contact:1", direction: 1 });
    document = applyEditorCommand(document, { type: "remove-contact", connectorId: "x", contactId: "x:contact:1" });
    expect(document.connectors[0]!.schematic.rowOrder).toEqual(["x:contact:2", "x:contact:3"]);
    expect(() => parseHarnessDesignDocument(document)).not.toThrow();
    for (const rowOrder of [["missing"], ["x:contact:2", "x:contact:2"]]) {
      expect(() => parseHarnessDesignDocument({ ...document, connectors: [{ ...document.connectors[0]!, schematic: { ...document.connectors[0]!.schematic, rowOrder } }] })).toThrow(/Порядок строк/);
    }
  });
  it("covers staggered bends from the first to the last contact span with matching drawn and electrical ports",()=>{
    const base=connectionDocument();
    const wires=base.wires.map((wire,index)=>({...wire,e4Route:index===0
      ? [{x:520,y:64},{x:520,y:40},{x:850,y:40},{x:850,y:64},{x:976,y:64}]
      : [{x:650,y:88},{x:650,y:120},{x:920,y:120},{x:920,y:88},{x:976,y:88}]}));
    let previous=-Infinity;
    for(let i=0;i<=100;i++){
      const document={...base,wires,screens:[{id:"screen",wireIds:["w1","w2"],position:i/100,width:46,label:"SH",terminalSide:"both" as const}]};
      const geometry=wireScreenConnectionGeometry(document,"screen")!;
      const painted=getE4ScreenLayout(document.screens[0]!,designToScene(document,"e4"))!;
      expect(geometry.orientation).toBe("horizontal");
      expect(geometry.center.x).toBeGreaterThan(previous);
      expect(painted.center).toEqual(geometry.center);
      expect(painted.terminals).toEqual(geometry.terminals);
      if(i===0)expect(geometry.center.x).toBeLessThan(520);
      if(i===100)expect(geometry.center.x).toBeGreaterThan(920);
      previous=geometry.center.x;
    }
  });

  it("routes carriers identically with and without a shield lead, and allows bends to a component contact",()=>{
    const base=connectionDocument();
    const command={type:"reroute-e4-wires" as const,wireIds:["w1","w2"]};
    const plain=applyEditorCommand(base,command);
    let shielded=applyEditorCommand(base,{type:"create-screen",screen:{id:"shield",wireIds:["w1","w2"],position:.1,width:46,label:"SH",terminalSide:"below"}});
    shielded=applyEditorCommand(shielded,{type:"add-wire",wire:createWire("lead",createScreenEndpoint("shield","below"),{connectorId:"x1",contactId:"x1:contact:3"})});
    const lead=shielded.wires.find(w=>w.id==="lead")!;
    expect(lead.e4Route.length).toBeGreaterThan(0);
    expect(()=>validateOrthogonalE4Route(wireEndpointE4Anchor(shielded,lead.from)!,lead.e4Route,wireEndpointE4Anchor(shielded,lead.to)!)).not.toThrow();
    shielded=applyEditorCommand(shielded,command);
    expect(shielded.wires.filter(w=>w.id!=="lead").map(w=>w.e4Route)).toEqual(plain.wires.map(w=>w.e4Route));
    const carriers=shielded.wires.filter(w=>w.id!=="lead");
    for(const position of [0,.5,1]){
      shielded=applyEditorCommand(shielded,{type:"update-screen",screenId:"shield",position});
      expect(shielded.wires.filter(w=>w.id!=="lead")).toEqual(carriers);
      expect(e4RoutingIssues(shielded)).toEqual([]);
      expect(()=>parseHarnessDesignDocument(JSON.parse(JSON.stringify(shielded)))).not.toThrow();
    }
  });
  it("refreshes compatible terminals without changing the pinned template identity", () => {
    const connector = templateConnector();
    let document = applyEditorCommand(createEmptyHarnessDesign(), { type: "add-connector", connector });
    document = applyEditorCommand(document, {
      type: "refresh-template-terminals",
      connectorId: connector.id,
      catalog: {
        templateId: "template-1", version: 4, versionSha256: "b".repeat(64),
        byContact: { "logical-1": ["T-3"] },
      },
    });
    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: connector.id, contactId: connector.contacts[0]!.id,
      terminalArticle: "T-3",
    });
    expect(document.connectors[0]!.libraryBinding).toMatchObject({
      mode: "template", templateId: "template-1", templateVersion: 3,
      versionSha256: "a".repeat(64), articleVariantId: "variant-1",
    });
    expect(document.connectors[0]!.contacts[0]!.terminalArticle).toBe("T-3");
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(document))).connectors[0]!.contacts[0]!.terminalArticle).toBe("T-3");
    expect(document.connectors[0]!.libraryBinding).toEqual(connector.libraryBinding);
  });

  it("keeps the full screen and braid outside tables, with the painted port exactly at the electrical endpoint", () => {
    const base = connectionDocument();
    for (const position of [0, 0.25, 0.8, 1]) {
      const document = applyEditorCommand(base, { type: "create-screen", screen: {
        id: "screen", wireIds: ["w1", "w2"], position, width: 46, label: "SH", terminalSide: "both",
      } });
      const scene = designToScene(document, "e4");
      const geometry = wireScreenConnectionGeometry(document, "screen")!;
      const painted = getE4ScreenLayout(document.screens[0]!, scene)!;
      expect(painted.center).toEqual(geometry.center);
      expect(painted.terminals).toEqual(geometry.terminals);
      expect(painted.center.x - painted.alongSize / 2).toBeGreaterThan(connectorE4TableGeometry(document.connectors[0]!).width);
      expect(painted.center.x + painted.alongSize / 2).toBeLessThan(document.connectors[1]!.positions.e4.x);
      const pair = getE4DifferentialPairLayout({ id: "pair", wireIds: ["w1", "w2"], step: 25, amplitude: 5 }, scene)!;
      expect(pair.motifs[0]!.coloredFrom).toBeGreaterThan(connectorE4TableGeometry(document.connectors[0]!).width);
      expect(pair.motifs.at(-1)!.coloredTo).toBeLessThan(document.connectors[1]!.positions.e4.x);
    }
  });

  it("changes a swatch without moving anchors or rejecting an existing routing collision", () => {
    const valid = singleWireConnectionDocument();
    const blocked = { ...valid, connectors: [...valid.connectors,
      createConnector("obstacle", "X3", 2, { x: 650, y: 30 })] };
    expect(e4RoutingIssues(blocked).length).toBeGreaterThan(0);
    const before = blocked.connectors.map(connectorE4TableGeometry);
    const changed = applyEditorCommand(blocked, { type: "update-contact", connectorId: "x1",
      contactId: "x1:contact:1", color: "Красный", secondaryColor: "Жёлтый" });
    expect(changed.connectors.map(connectorE4TableGeometry)).toEqual(before);
    expect(changed.wires[0]!.color).not.toBe(blocked.wires[0]!.color);
    expect(changed.wires[0]!.e4Route).toEqual(blocked.wires[0]!.e4Route);
    expect(changed.connectors[1]!.contacts[0]).toMatchObject({ color: "Красный", secondaryColor: "Жёлтый" });
    expect(() => parseHarnessDesignDocument(JSON.parse(JSON.stringify(changed)))).not.toThrow();
  });

  it("commits a temporarily blocked drag, survives reload and repairs on the next drag", () => {
    const valid = singleWireConnectionDocument();
    const blocked = applyEditorCommand(valid, { type: "move-connector", connectorId: "x2", view: "e4",
      position: { x: 200, y: 30 } });
    expect(blocked.connectors[1]!.positions.e4).toEqual({ x: 200, y: 30 });
    expect(e4RoutingIssues(blocked).length).toBeGreaterThan(0);
    const restored = parseHarnessDesignDocument(JSON.parse(JSON.stringify(blocked)));
    const repaired = applyEditorCommand(restored, { type: "move-connector", connectorId: "x2", view: "e4",
      position: { x: 1200, y: 0 } });
    expect(e4RoutingIssues(repaired)).toEqual([]);
    expect(repaired.wires[0]!.from).toEqual(valid.wires[0]!.from);
    expect(repaired.wires[0]!.to).toEqual(valid.wires[0]!.to);
  });

  it("projects the screen branch straight onto its target and follows screen movement", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, { type: "create-screen",
      screen: { id: "screen", wireIds: ["w2"], position: 0.5, width: 16, label: "SH" } });
    document = applyEditorCommand(document, { type: "create-junction",
      junction: { id: "j", position: { x: 850, y: 64 }, wireIds: ["w1", "branch"] },
      branchWire: createWire("branch", createScreenEndpoint("screen"), createJunctionEndpoint("j")) });
    const port = wireEndpointE4Anchor(document, createScreenEndpoint("screen"))!.position;
    expect(document.junctions[0]!.position).toEqual({ x: port.x, y: 64 });
    expect(document.wires.find((wire) => wire.id === "branch")!.e4Route).toEqual([]);
    expect(() => parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)))).not.toThrow();
    const moved = applyEditorCommand(document, { type: "update-screen", screenId: "screen", position: 0.7 });
    const movedPort = wireEndpointE4Anchor(moved, createScreenEndpoint("screen"))!.position;
    expect(movedPort.x).not.toBe(port.x);
    expect(moved.junctions[0]!.position).toEqual({ x: movedPort.x, y: 64 });
    expect(moved.wires.find((wire) => wire.id === "branch")!.e4Route).toEqual([]);
    expect(e4RoutingIssues(moved)).toEqual([]);
    expect(() => applyEditorCommand(moved, { type: "set-e4-wire-route", wireId: "branch",
      route: [{ x: movedPort.x, y: 40 }, { x: 850, y: 40 }] })).toThrow(/прямым/);
    expect(()=>applyEditorCommand(moved,{type:"edit-e4-bend",wireId:"branch",index:0,position:{x:movedPort.x,y:40},insert:true,mode:"adjacent"})).toThrow(/прямым/);
  });

  it("keeps E4 and drawing positions separate while sharing one connector", () => {
    const connector = createConnector("x1", "X1", 2, { x: 10, y: 20 }, { x: 40, y: 50 });
    const added = applyEditorCommand(createEmptyHarnessDesign(), { type: "add-connector", connector });
    const moved = applyEditorCommand(added, {
      type: "move-connector", connectorId: "x1", view: "drawing", position: { x: 90, y: 110 },
    });
    expect(moved.connectors[0]?.positions.e4).toEqual({ x: 10, y: 20 });
    expect(moved.connectors[0]?.positions.drawing).toEqual({ x: 90, y: 110 });
  });

  it("applies a series article atomically and protects its deterministic rows", () => {
    const connector = createBuiltInConnectorInstance("catalog-connector-series:xs-demo-series", {
      id: "xs1", designation: "XS1", e4Position: { x: 0, y: 0 },
    });
    let document = applyEditorCommand(createEmptyHarnessDesign(), { type: "add-connector", connector });
    const selected = selectConnectorSeriesArticle(connector, builtInConnectorSeries[0]!, "XS-10").connector;
    document = applyEditorCommand(document, {
      type: "apply-connector-article",
      connectorId: connector.id,
      partNumber: selected.partNumber,
      contacts: selected.contacts,
      libraryBinding: selected.libraryBinding,
    });
    expect(document.connectors[0]).toMatchObject({
      partNumber: "XS-10",
      libraryBinding: { mode: "series", seriesId: "xs-demo-series", partNumber: "XS-10" },
    });
    expect(document.connectors[0]?.contacts).toHaveLength(10);
    expect(() => applyEditorCommand(document, {
      type: "update-contact", connectorId: "xs1", contactId: "xs1:contact:signal:1", number: 99,
    })).toThrow(/определяются выбранным артикулом/);
    expect(() => applyEditorCommand(document, {
      type: "remove-contact", connectorId: "xs1", contactId: "xs1:contact:signal:1",
    })).toThrow(/определяются выбранным артикулом/);
  });

  it("protects template structure while allowing instance electrical fields and compatible terminals", () => {
    const connector = templateConnector();
    let document = applyEditorCommand(createEmptyHarnessDesign(), { type: "add-connector", connector });

    document = applyEditorCommand(document, {
      type: "update-connector", connectorId: connector.id, designation: "X2",
    });
    document = applyEditorCommand(document, {
      type: "update-contact",
      connectorId: connector.id,
      contactId: connector.contacts[0]!.id,
      circuit: "CAN-H",
      terminalArticle: "T-2",
      wire: "UL1061 28AWG",
      color: "красный",
      secondaryColor: "белый",
    });

    expect(document.connectors[0]).toMatchObject({
      designation: "X2",
      libraryCode: "JST-XH",
      partNumber: "XH-1",
      libraryBinding: connector.libraryBinding,
    });
    expect(document.connectors[0]?.contacts[0]).toMatchObject({
      logicalContactId: "logical-1",
      number: 1,
      contactType: "сигнальный",
      circuit: "CAN-H",
      terminalArticle: "T-2",
      wire: "UL1061 28AWG",
      color: "красный",
      secondaryColor: "белый",
    });
    expect(() => applyEditorCommand(document, {
      type: "update-contact", connectorId: connector.id, contactId: connector.contacts[0]!.id,
      terminalArticle: "T-OTHER",
    })).toThrow(/совместимых терминалов/);
    expect(() => applyEditorCommand(document, {
      type: "update-contact", connectorId: connector.id, contactId: connector.contacts[0]!.id, number: 2,
    })).toThrow(/библиотечного контакта/);
    expect(() => applyEditorCommand(document, {
      type: "update-contact", connectorId: connector.id, contactId: connector.contacts[0]!.id, contactType: "силовой",
    })).toThrow(/библиотечного контакта/);
    expect(() => applyEditorCommand(document, {
      type: "update-connector", connectorId: connector.id, designation: "X2", partNumber: "FORGED",
    })).toThrow(/закреплённого шаблона/);
    expect(() => applyEditorCommand(document, {
      type: "update-connector", connectorId: connector.id, designation: "X2", libraryCode: "FORGED",
    })).toThrow(/определяется справочником/);
    expect(() => applyEditorCommand(document, {
      type: "remove-contact", connectorId: connector.id, contactId: connector.contacts[0]!.id,
    })).toThrow(/библиотечного соединителя/);
    expect(() => applyEditorCommand(document, {
      type: "add-contact", connectorId: connector.id, contact: { ...connector.contacts[0]!, id: "new", number: 2 },
    })).toThrow(/библиотечного соединителя/);
  });

  it("accepts 512-character connector articles and rejects longer values", () => {
    const article = "A".repeat(512);
    const connector = createConnector("long", "X1", 1, { x: 0, y: 0 }, undefined, article);
    const document = applyEditorCommand(createEmptyHarnessDesign(), { type: "add-connector", connector });
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(document))).connectors[0]?.partNumber).toBe(article);
    expect(() => createConnector("too-long", "X2", 1, { x: 0, y: 0 }, undefined, `${article}A`))
      .toThrow(/512/);
  });

  it("keeps wires on retained series positions and rejects an article that removes a wired position", () => {
    const series = builtInConnectorSeries[0]!;
    const xs10 = createBuiltInConnectorInstance("catalog-xs-10", {
      id: "xs1", designation: "XS1", e4Position: { x: 0, y: 0 },
    });
    const mate = createConnector("x2", "X2", 2, { x: 1000, y: 0 });
    let document = applyEditorCommand(createEmptyHarnessDesign(), { type: "add-connector", connector: xs10 });
    document = applyEditorCommand(document, { type: "add-connector", connector: mate });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("keep", { connectorId: "xs1", contactId: "xs1:contact:signal:1" }, { connectorId: "x2", contactId: "x2:contact:1" }),
    });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("remove", { connectorId: "xs1", contactId: "xs1:contact:signal:6" }, { connectorId: "x2", contactId: "x2:contact:2" }),
    });
    const xs04 = selectConnectorSeriesArticle(xs10, series, "XS-04").connector;
    const command = {
      type: "apply-connector-article" as const,
      connectorId: xs10.id,
      partNumber: xs04.partNumber,
      contacts: xs04.contacts,
      libraryBinding: xs04.libraryBinding,
    };

    expect(() => applyEditorCommand(document, command)).toThrow(/^Выбранный артикул удалит подключённые контакты/);
    expect(document.connectors[0]?.partNumber).toBe("XS-10");
    document = applyEditorCommand(document, { type: "remove-wire", wireId: "remove" });
    document = applyEditorCommand(document, command);
    expect(document.connectors[0]?.partNumber).toBe("XS-04");
    expect(document.wires[0]?.from).toEqual({ connectorId: "xs1", contactId: "xs1:contact:signal:1" });
  });

  it("rejects inconsistent series article commands and forged free library positions", () => {
    const connector = createBuiltInConnectorInstance("catalog-xs-04", {
      id: "xs1", designation: "XS1", e4Position: { x: 0, y: 0 },
    });
    let document = applyEditorCommand(createEmptyHarnessDesign(), { type: "add-connector", connector });
    expect(() => applyEditorCommand(document, {
      type: "apply-connector-article",
      connectorId: connector.id,
      partNumber: "XS-10",
      contacts: connector.contacts,
      libraryBinding: { mode: "series", seriesId: "xs-demo-series", partNumber: "XS-04" },
    })).toThrow(/не совпадает/);
    expect(() => applyEditorCommand(document, {
      type: "apply-connector-article",
      connectorId: connector.id,
      partNumber: "XS-04",
      contacts: connector.contacts,
      libraryBinding: { mode: "series", seriesId: "another-series", partNumber: "XS-04" },
    })).toThrow(/заменить серию/);
    expect(() => applyEditorCommand(document, {
      type: "update-connector", connectorId: connector.id, designation: "XS1", partNumber: "FORGED",
    })).toThrow(/выбором артикула/);

    const free = createConnector("free", "X1", 1, { x: 0, y: 0 });
    document = applyEditorCommand(createEmptyHarnessDesign(), { type: "add-connector", connector: free });
    expect(() => applyEditorCommand(document, {
      type: "add-contact",
      connectorId: free.id,
      contact: {
        ...free.contacts[0]!, id: "free:contact:signal:2", number: 2,
        libraryContact: { kind: "signal", ordinal: 2 },
      },
    })).toThrow(/Свободная строка/);
  });

  it("connects existing contacts and removes their wires with the connector", () => {
    const x1 = createConnector("x1", "X1", 2, { x: 0, y: 0 });
    const x2 = createConnector("x2", "X2", 2, { x: 800, y: 0 });
    let document = applyEditorCommand(createEmptyHarnessDesign(), { type: "add-connector", connector: x1 });
    document = applyEditorCommand(document, { type: "add-connector", connector: x2 });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:2" }, 350),
    });
    expect(document.wires[0]?.lengthMm).toBe(350);
    document = applyEditorCommand(document, { type: "remove-connector", connectorId: "x1" });
    expect(document.wires).toHaveLength(0);
  });

  it("does not change absolute length when route geometry changes", () => {
    const x1 = createConnector("x1", "X1", 1, { x: 0, y: 0 });
    const x2 = createConnector("x2", "X2", 1, { x: 800, y: 0 });
    let document = createEmptyHarnessDesign();
    document = applyEditorCommand(document, { type: "add-connector", connector: x1 });
    document = applyEditorCommand(document, { type: "add-connector", connector: x2 });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }, 350),
    });
    document = applyEditorCommand(document, { type: "set-wire-route", wireId: "w1", route: [{ x: 100, y: 90 }] });
    expect(document.wires[0]?.drawingRoute).toEqual([{ x: 100, y: 90 }]);
    expect(document.wires[0]?.lengthMm).toBe(350);
  });

  it("updates structured wire length fields atomically and supports an unknown source length", () => {
    const x1 = createConnector("x1", "X1", 1, { x: 0, y: 0 });
    const x2 = createConnector("x2", "X2", 1, { x: 800, y: 0 });
    let document = createEmptyHarnessDesign();
    document = applyEditorCommand(document, { type: "add-connector", connector: x1 });
    document = applyEditorCommand(document, { type: "add-connector", connector: x2 });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }),
    });
    document = applyEditorCommand(document, {
      type: "update-wire",
      wireId: "w1",
      lengthMm: null,
      endCorrectionFromMm: -2,
      endCorrectionToMm: 3,
      cutRoundingStepMm: 0.5,
    });
    expect(document.wires[0]).toMatchObject({
      lengthMm: null,
      endCorrectionFromMm: -2,
      endCorrectionToMm: 3,
      cutRoundingStepMm: 0.5,
    });
    expect(calculateWireCutLength(document.wires[0]!)).toMatchObject({
      unroundedTotalMm: null,
      cutLengthMm: null,
      materialConsumptionMm: null,
      isComplete: false,
    });

    expect(() => applyEditorCommand(document, {
      type: "update-wire", wireId: "w1", lengthMm: 1, endCorrectionFromMm: -2, endCorrectionToMm: 0,
    })).toThrow(/не должна быть отрицательной/);
    expect(document.wires[0]?.lengthMm).toBeNull();
  });

  it("assigns, replaces and clears one immutable material binding without changing wire geometry", () => {
    const x1 = createConnector("x1", "X1", 1, { x: 0, y: 0 });
    const x2 = createConnector("x2", "X2", 1, { x: 800, y: 0 });
    let document = createEmptyHarnessDesign();
    document = applyEditorCommand(document, { type: "add-connector", connector: x1 });
    document = applyEditorCommand(document, { type: "add-connector", connector: x2 });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }, 350),
    });
    const route = document.wires[0]!.e4Route;
    const material = {
      sourceId: "technology-database",
      snapshotId: "38d9aa91-b8d4-45d8-8e39-da14ee4effad",
      snapshotSha256: "a".repeat(64), recordId: "b".repeat(64), entityType: "wire" as const,
      sourceKey: "UL1061-24AWG", displayName: "UL1061 24AWG",
    };

    document = applyEditorCommand(document, { type: "update-wire", wireId: "w1", materialBinding: material });
    expect(document.wires[0]!.materialBinding).toEqual(material);
    expect(document.wires[0]!.e4Route).toEqual(route);
    document = applyEditorCommand(document, { type: "update-wire", wireId: "w1", materialBinding: { ...material, sourceKey: "НВ-4-0,2", displayName: "НВ-4 0,2 мм²" } });
    expect(document.wires[0]!.materialBinding?.sourceKey).toBe("НВ-4-0,2");
    document = applyEditorCommand(document, { type: "update-wire", wireId: "w1", materialBinding: null });
    expect(document.wires[0]!.materialBinding).toBeUndefined();
  });

  it("calculates cut length in exact millimetres and rounds only the final sum upwards", () => {
    const result = calculateWireCutLength({
      lengthMm: 1000.125,
      endCorrectionFromMm: -0.025,
      endCorrectionToMm: 0.1,
      cutRoundingStepMm: 0.5,
    });
    expect(result).toEqual({
      sourceLengthMm: 1000.125,
      endCorrectionFromMm: -0.025,
      endCorrectionToMm: 0.1,
      cutRoundingStepMm: 0.5,
      unroundedTotalMm: 1000.2,
      cutLengthMm: 1000.5,
      materialConsumptionMm: 1000.5,
      isComplete: true,
    });
    expect(calculateWireCutLength({
      lengthMm: 0,
      endCorrectionFromMm: 0,
      endCorrectionToMm: 0,
      cutRoundingStepMm: 1,
    }).cutLengthMm).toBe(0);
    expect(calculateWireCutLength({
      lengthMm: 1.001,
      endCorrectionFromMm: 0,
      endCorrectionToMm: 0,
      cutRoundingStepMm: 0.001,
    }).cutLengthMm).toBe(1.001);
    expect(() => calculateWireCutLength({
      lengthMm: 1.0001,
      endCorrectionFromMm: 0,
      endCorrectionToMm: 0,
      cutRoundingStepMm: 1,
    })).toThrow(/0,001 мм/);
  });

  it("normalizes legacy and incomplete wire length JSON without inventing a physical value", () => {
    const x1 = createConnector("x1", "X1", 1, { x: 0, y: 0 });
    const x2 = createConnector("x2", "X2", 1, { x: 800, y: 0 });
    const base = {
      ...createEmptyHarnessDesign(),
      connectors: [x1, x2],
    };
    const currentWire = {
      ...createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }, 350),
      e4Route: createOrthogonalE4Route(
        wireEndpointE4Anchor({ ...base, wires: [] }, { connectorId: "x1", contactId: "x1:contact:1" })!,
        wireEndpointE4Anchor({ ...base, wires: [] }, { connectorId: "x2", contactId: "x2:contact:1" })!,
      ),
    };
    const {
      endCorrectionFromMm: _from,
      endCorrectionToMm: _to,
      cutRoundingStepMm: _step,
      ...legacyWire
    } = currentWire;
    const legacy = parseHarnessDesignDocument({ ...base, wires: [legacyWire] });
    expect(legacy.wires[0]).toMatchObject({
      lengthMm: 350,
      endCorrectionFromMm: 0,
      endCorrectionToMm: 0,
      cutRoundingStepMm: 1,
    });

    const structured = parseHarnessDesignDocument({
      ...base,
      wires: [{
        ...legacyWire,
        lengthMm: 350.125,
        endCorrectionFromMm: -0.025,
        endCorrectionToMm: 0.4,
        cutRoundingStepMm: 0.5,
      }],
    });
    expect(structured.wires[0]).toMatchObject({
      lengthMm: 350.125,
      endCorrectionFromMm: -0.025,
      endCorrectionToMm: 0.4,
      cutRoundingStepMm: 0.5,
    });

    const { lengthMm: _length, ...wireWithoutLength } = legacyWire;
    const incomplete = parseHarnessDesignDocument({ ...base, wires: [wireWithoutLength] });
    expect(incomplete.wires[0]).toMatchObject({
      lengthMm: null,
      endCorrectionFromMm: 0,
      endCorrectionToMm: 0,
      cutRoundingStepMm: 1,
    });
    expect(() => parseHarnessDesignDocument({
      ...base,
      wires: [{ ...legacyWire, cutRoundingStepMm: 0 }],
    })).toThrow(/Шаг округления/);
  });

  it("reconnects one end of an existing wire and preserves its properties", () => {
    const x1 = createConnector("x1", "X1", 2, { x: 0, y: 0 });
    const x2 = createConnector("x2", "X2", 2, { x: 800, y: 0 });
    let document = createEmptyHarnessDesign();
    document = applyEditorCommand(document, { type: "add-connector", connector: x1 });
    document = applyEditorCommand(document, { type: "add-connector", connector: x2 });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }, 420, "ЦЕПЬ-1", "#cc0000"),
    });

    document = applyEditorCommand(document, {
      type: "reconnect-wire",
      wireId: "w1",
      end: "to",
      endpoint: { connectorId: "x2", contactId: "x2:contact:2" },
    });

    expect(document.wires[0]).toMatchObject({
      to: { connectorId: "x2", contactId: "x2:contact:2" },
      lengthMm: 420,
      circuit: "ЦЕПЬ-1",
      color: "#cc0000",
    });
  });

  it("undoes and redoes complete document commands", () => {
    let history = createEditorHistory(createEmptyHarnessDesign());
    history = executeEditorCommand(history, {
      type: "add-connector", connector: createConnector("x1", "X1", 1, { x: 0, y: 0 }),
    });
    expect(history.present.connectors).toHaveLength(1);
    history = undoEditorCommand(history);
    expect(history.present.connectors).toHaveLength(0);
    history = redoEditorCommand(history);
    expect(history.present.connectors).toHaveLength(1);
  });

  it("creates an E4 table presentation with independent template article and orientation", () => {
    const connector = createConnector("x1", "XS1", 2, { x: 0, y: 0 }, undefined, "SH-001");
    expect(connector.designation).toBe("XS1");
    expect(connector.partNumber).toBe("SH-001");
    expect(connector.schematic.orientation).toBe("contacts-right");
    expect(connector.schematic.baseColumns.map((column) => column.key)).toEqual([
      "number", "contactType", "circuit", "terminal", "wire", "wireSection", "color",
    ]);
    expect(connector.contacts[0]).toMatchObject({
      contactType: "", terminalArticle: "", wire: "", color: "", connectionStatus: "available", customValues: {},
    });
    let document = applyEditorCommand(createEmptyHarnessDesign(), { type: "add-connector", connector });
    document = applyEditorCommand(document, {
      type: "update-connector", connectorId: "x1", designation: "XP1", partNumber: "BS1071-7",
    });
    expect(document.connectors[0]).toMatchObject({ designation: "XP1", partNumber: "BS1071-7" });
    expect(() => applyEditorCommand(document, {
      type: "update-connector", connectorId: "x1", designation: "XP1", partNumber: " ",
    })).toThrow(/артикул/);
  });

  it("stores the free footer code separately from its article and preserves legacy FREE", () => {
    let document = applyEditorCommand(createEmptyHarnessDesign(), {
      type: "add-connector", connector: createConnector("free", "X1", 1, { x: 0, y: 0 }, undefined, "ARTICLE-1"),
    });
    document = applyEditorCommand(document, {
      type: "update-connector",
      connectorId: "free",
      designation: "X1",
      libraryCode: "CUSTOM",
      partNumber: "ARTICLE-2",
    });
    expect(document.connectors[0]).toMatchObject({
      libraryCode: "CUSTOM", partNumber: "ARTICLE-2",
    });
    const legacy = structuredClone(document) as unknown as { connectors: Array<Record<string, unknown>> };
    delete legacy.connectors[0]!.libraryCode;
    expect(parseHarnessDesignDocument(legacy).connectors[0]?.libraryCode).toBe("FREE");
  });

  it("adds a row to a newly placed FREE connector without an intermediate edit", () => {
    const placed = createBuiltInConnectorInstance("catalog-connector-free", {
      id: "free-new", designation: "X1", e4Position: { x: 50, y: 60 },
    });
    let document = applyEditorCommand(createEmptyHarnessDesign(), { type: "add-connector", connector: placed });
    document = applyEditorCommand(document, {
      type: "add-contact",
      connectorId: placed.id,
      contact: {
        id: "free-new:contact:5", number: 5, contactType: "", circuit: "", terminalArticle: "",
        wire: "", color: "", secondaryColor: "", connectionStatus: "available", customValues: {},
      },
    });
    expect(document.connectors[0]?.contacts.map((contact) => contact.number)).toEqual([1, 2, 3, 4, 5]);
  });

  it("keeps the E4 document usable after entering a wire reference on a connected contact", () => {
    let document = applyEditorCommand(createEmptyHarnessDesign(), {
      type: "add-connector", connector: createConnector("x1", "X1", 1, { x: 0, y: 0 }),
    });
    document = applyEditorCommand(document, {
      type: "add-connector", connector: createConnector("x2", "X2", 1, { x: 800, y: 0 }),
    });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }),
    });
    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x1", contactId: "x1:contact:1", wire: "UL1061 28AWG",
    });
    expect(document.connectors[0]?.contacts[0]?.wire).toBe("UL1061 28AWG");
    expect(document.wires[0]?.e4Route.length).toBeGreaterThan(0);
    const restored = parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)));
    expect(restored.connectors[0]?.contacts[0]?.wire).toBe("UL1061 28AWG");
    expect(restored.wires[0]?.e4Route).toEqual(document.wires[0]?.e4Route);
  });

  it("supports flipping orientation, contact status, custom fields and safe contact removal", () => {
    let document = applyEditorCommand(createEmptyHarnessDesign(), {
      type: "add-connector", connector: createConnector("x1", "XS1", 2, { x: 0, y: 0 }),
    });
    document = applyEditorCommand(document, {
      type: "add-custom-field", connectorId: "x1", field: { id: "note", label: "Примечание", visible: true },
    });
    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x1", contactId: "x1:contact:1",
      contactType: "сигнальный", terminalArticle: "SH-001", wire: "UL1061 28AWG", color: "чёрный",
      connectionStatus: "not-connected",
      customValues: { note: "оператор" },
    });
    document = applyEditorCommand(document, { type: "flip-connector-orientation", connectorId: "x1" });
    document = applyEditorCommand(document, {
      type: "toggle-base-column-visibility", connectorId: "x1", key: "number",
    });
    document = applyEditorCommand(document, {
      type: "add-contact",
      connectorId: "x1",
      contact: {
        id: "x1:contact:3", number: 3, contactType: "силовой", circuit: "L1",
        terminalArticle: "SH-003", wire: "UL1061 20AWG", color: "синий",
        connectionStatus: "available", customValues: { note: "резерв" },
      },
    });
    expect(document.connectors[0]?.schematic.orientation).toBe("contacts-left");
    expect(document.connectors[0]?.schematic.baseColumns.find((column) => column.key === "number")?.visible).toBe(false);
    expect(document.connectors[0]?.contacts[0]).toMatchObject({
      contactType: "сигнальный", terminalArticle: "SH-001", wire: "UL1061 28AWG", color: "чёрный",
      connectionStatus: "not-connected", customValues: { note: "оператор" },
    });
    document = applyEditorCommand(document, { type: "remove-contact", connectorId: "x1", contactId: "x1:contact:3" });
    expect(document.connectors[0]?.contacts.map((contact) => contact.number)).toEqual([1, 2]);
    document = applyEditorCommand(document, { type: "remove-custom-field", connectorId: "x1", fieldId: "note" });
    expect(document.connectors[0]?.contacts[0]?.customValues).toEqual({});
  });

  it("does not remove a contact referenced by a wire and parses legacy contacts with defaults", () => {
    const legacy = createEmptyHarnessDesign();
    const x1 = createConnector("x1", "X1", 1, { x: 0, y: 0 });
    const x2 = createConnector("x2", "X2", 1, { x: 200, y: 0 });
    const { partNumber: _partNumber, schematic: _schematic, libraryBinding: _libraryBinding, ...legacyX1 } = x1;
    const legacyJson = {
      ...legacy,
      connectors: [
        { ...legacyX1, contacts: [{ id: "x1:contact:1", number: 1, circuit: "" }] },
        x2,
      ],
      wires: [(() => {
        const { e4Route: _e4Route, ...wire } = createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" });
        return wire;
      })()],
    };
    const parsed = parseHarnessDesignDocument(legacyJson);
    expect(parsed.connectors[0]?.partNumber).toBe("X1");
    expect(parsed.connectors[0]).toMatchObject({ libraryBinding: { mode: "free" } });
    expect(parsed.connectors[0]?.contacts[0]).toMatchObject({
      wire: "", color: "", connectionStatus: "available", customValues: {}, libraryContact: null,
    });
    expect(() => applyEditorCommand(parsed, { type: "remove-contact", connectorId: "x1", contactId: "x1:contact:1" })).toThrow(/подключён/);
  });

  it("rejects inconsistent library metadata while parsing schema version 1", () => {
    const base = createEmptyHarnessDesign();
    const seriesConnector = createBuiltInConnectorInstance("catalog-xs-04", {
      id: "xs1", designation: "XS1", e4Position: { x: 0, y: 0 },
    });
    expect(() => parseHarnessDesignDocument({
      ...base,
      connectors: [{ ...seriesConnector, libraryBinding: { ...seriesConnector.libraryBinding!, partNumber: "XS-10" } }],
    })).toThrow(/Артикул соединителя не совпадает/);
    expect(() => parseHarnessDesignDocument({
      ...base,
      connectors: [{ ...seriesConnector, contacts: seriesConnector.contacts.map((contact, index) =>
        index === 0 ? { ...contact, libraryContact: undefined } : contact) }],
    })).toThrow(/отсутствует позиция/);
    const free = createConnector("free", "X1", 1, { x: 0, y: 0 });
    expect(() => parseHarnessDesignDocument({
      ...base,
      connectors: [{
        ...free,
        contacts: [{ ...free.contacts[0], libraryContact: { kind: "signal", ordinal: 1 } }],
      }],
    })).toThrow(/Свободный соединитель/);
  });

  it("enforces the electrical meaning of a not-connected contact", () => {
    let document = createEmptyHarnessDesign();
    document = applyEditorCommand(document, {
      type: "add-connector", connector: createConnector("x1", "X1", 2, { x: 0, y: 0 }),
    });
    document = applyEditorCommand(document, {
      type: "add-connector", connector: createConnector("x2", "X2", 2, { x: 800, y: 0 }),
    });
    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x1", contactId: "x1:contact:1", connectionStatus: "not-connected",
    });
    expect(() => applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }),
    })).toThrow(/неподключённый/);

    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w2", { connectorId: "x1", contactId: "x1:contact:2" }, { connectorId: "x2", contactId: "x2:contact:1" }),
    });
    expect(() => applyEditorCommand(document, {
      type: "update-contact", connectorId: "x1", contactId: "x1:contact:2", connectionStatus: "not-connected",
    })).toThrow(/пока к нему подключён провод/);
    expect(() => applyEditorCommand(document, {
      type: "reconnect-wire", wireId: "w2", end: "to",
      endpoint: { connectorId: "x1", contactId: "x1:contact:1" },
    })).toThrow(/неподключённый/);
  });

  it("derives mirrored E4 table geometry and keeps drawing geometry unchanged", () => {
    let document = applyEditorCommand(createEmptyHarnessDesign(), {
      type: "add-connector", connector: createConnector("x1", "XS1", 2, { x: 10, y: 20 }, { x: 30, y: 40 }),
    });
    const right = connectorE4TableGeometry(document.connectors[0]!);
    expect(right.columns.map((column) => column.kind === "base" ? column.key : column.id)).toEqual([
      "color", "wireSection", "wire", "terminal", "circuit", "contactType", "number",
    ]);
    expect(connectorContactPosition(document.connectors[0]!, "x1:contact:1", "e4")).toEqual({
      x: 10 + right.width,
      y: 20 + right.titleHeight + right.headerHeight + right.rowHeight / 2,
    });
    expect(connectorContactPosition(document.connectors[0]!, "x1:contact:1", "drawing")).toEqual({ x: 148, y: 68 });

    document = applyEditorCommand(document, { type: "flip-connector-orientation", connectorId: "x1" });
    const left = connectorE4TableGeometry(document.connectors[0]!);
    expect(left.columns.map((column) => column.kind === "base" ? column.key : column.id)).toEqual([
      "number", "contactType", "circuit", "terminal", "wire", "wireSection", "color",
    ]);
    expect(connectorContactPosition(document.connectors[0]!, "x1:contact:1", "e4")?.x).toBe(10);
    expect(left.height).toBe(left.titleHeight + left.headerHeight + 2 * left.rowHeight + left.footerHeight);
  });

  it("sizes E4 columns to their visible content with a bounded maximum", () => {
    const short = createConnector("short", "X1", 2, { x: 0, y: 0 });
    const long = {
      ...short,
      contacts: short.contacts.map((contact, index) => index === 0
        ? { ...contact, circuit: "Длинное текстовое обозначение цепи" }
        : contact),
    };
    const shortCircuit = connectorE4TableGeometry(short).columns.find((column) => column.kind === "base" && column.key === "circuit")!;
    const longCircuit = connectorE4TableGeometry(long).columns.find((column) => column.kind === "base" && column.key === "circuit")!;
    expect(longCircuit.width).toBeGreaterThan(shortCircuit.width);
    expect(longCircuit.width).toBeLessThanOrEqual(220);
  });

  it("does not widen the contact-number column for a long article in the footer", () => {
    const shortArticle = createConnector("short", "X1", 12, { x: 0, y: 0 });
    const longArticle = { ...shortArticle, partNumber: "XHP-2(10.0)-U-WITH-A-LONG-SUFFIX" };
    const numberWidth = (connector: typeof shortArticle) => connectorE4TableGeometry(connector).columns
      .find((column) => column.kind === "base" && column.key === "number")!.width;
    expect(numberWidth(longArticle)).toBe(numberWidth(shortArticle));
  });

  it("moves an E4 wire label without changing that wire topology", () => {
    const before = connectionDocument();
    const wireBefore = before.wires.find((wire) => wire.id === "w1")!;
    const after = applyEditorCommand(before, {
      type: "set-e4-wire-label-position", wireId: "w1", position: 0.2,
    });
    const wireAfter = after.wires.find((wire) => wire.id === "w1")!;
    expect(wireAfter.e4LabelPosition).toBe(0.2);
    expect(wireAfter.from).toEqual(wireBefore.from);
    expect(wireAfter.to).toEqual(wireBefore.to);
    expect(wireAfter.e4Route).toEqual(wireBefore.e4Route);
    expect(() => applyEditorCommand(after, {
      type: "set-e4-wire-label-position", wireId: "w1", position: 2,
    })).toThrow(/от 0 до 1/);
  });

  it("creates and validates orthogonal E4 routes with 24-unit contact leads", () => {
    const start = { position: { x: 100, y: 50 }, leadDirection: "right" as const };
    const end = { position: { x: 300, y: 90 }, leadDirection: "left" as const };
    const route = createOrthogonalE4Route(start, end);
    expect(route[0]).toEqual({ x: 124, y: 50 });
    expect(route.at(-1)).toEqual({ x: 276, y: 90 });
    expect(() => validateOrthogonalE4Route(start, route, end)).not.toThrow();
    expect(() => validateOrthogonalE4Route(start, [{ x: 110, y: 50 }, { x: 300, y: 90 }], end)).toThrow(/ортогональных|прямой участок/);
  });

  it("reroutes independent wires with clearance and around every connector table", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w3", { connectorId: "x1", contactId: "x1:contact:3" }, { connectorId: "x2", contactId: "x2:contact:3" }),
    });
    document = applyEditorCommand(document, {
      type: "add-connector",
      connector: createConnector("obstacle", "X3", 1, { x: 500, y: -180 }),
    });
    document = applyEditorCommand(document, {
      type: "move-connector", connectorId: "obstacle", view: "e4", position: { x: 500, y: 40 },
    });

    const fullRoute = (wireId: string) => {
      const wire = document.wires.find((item) => item.id === wireId)!;
      return [
        connectorContactPosition(document.connectors.find((item) => item.id === wire.from.connectorId)!, wire.from.contactId, "e4")!,
        ...wire.e4Route,
        connectorContactPosition(document.connectors.find((item) => item.id === wire.to.connectorId)!, wire.to.contactId, "e4")!,
      ];
    };
    const obstacle = document.connectors.find((item) => item.id === "obstacle")!;
    const obstacleGeometry = connectorE4TableGeometry(obstacle);
    for (const wireId of ["w1", "w2", "w3"]) {
      const points = fullRoute(wireId);
      expect(() => validateE4Route(points, {
        start: { position: points[0]!, leadDirection: "right", obstacleId: "x1" },
        end: { position: points.at(-1)!, leadDirection: "left", obstacleId: "x2" },
        obstacles: [{
          id: obstacle.id,
          x: obstacle.positions.e4.x,
          y: obstacle.positions.e4.y,
          width: obstacleGeometry.width,
          height: obstacleGeometry.height,
        }],
        occupiedRoutes: ["w1", "w2", "w3"].filter((id) => id !== wireId).map((id) => ({ id, points: fullRoute(id) })),
        options: { wireClearance: 8, leadLength: 24 },
      })).not.toThrow();
    }
  });

  it("reoptimizes automatic routes after an obstacle moves away and preserves manual routes", () => {
    let document = createEmptyHarnessDesign();
    document = applyEditorCommand(document, {
      type: "add-connector", connector: createConnector("x1", "X1", 1, { x: 0, y: 0 }),
    });
    document = applyEditorCommand(document, {
      type: "add-connector", connector: createConnector("x2", "X2", 1, { x: 1000, y: 0 }),
    });
    document = applyEditorCommand(document, { type: "flip-connector-orientation", connectorId: "x2" });
    document = applyEditorCommand(document, {
      type: "add-connector", connector: createConnector("obstacle", "X3", 1, { x: 500, y: 40 }),
    });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("automatic", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }),
    });
    const detour = document.wires[0]!.e4Route;
    expect(document.wires[0]?.e4RouteMode).toBe("auto");

    document = applyEditorCommand(document, {
      type: "move-connector", connectorId: "obstacle", view: "e4", position: { x: 500, y: 300 },
    });
    expect(document.wires[0]?.e4Route).not.toEqual(detour);

    const autoRoute = document.wires[0]!.e4Route;
    document = applyEditorCommand(document, { type: "set-e4-wire-route", wireId: "automatic", route: autoRoute });
    expect(document.wires[0]?.e4RouteMode).toBe("manual");
    document = applyEditorCommand(document, {
      type: "move-connector", connectorId: "obstacle", view: "e4", position: { x: 500, y: 500 },
    });
    expect(document.wires[0]?.e4Route).toEqual(autoRoute);
  });

  it("reoptimizes every automatic wire after a connector lands on a snapped position", () => {
    let document = connectionDocument();
    const needlessHump = [
      { x: 648, y: 88 }, { x: 700, y: 88 }, { x: 700, y: 200 },
      { x: 900, y: 200 }, { x: 900, y: 88 }, { x: 976, y: 88 },
    ];
    document = {
      ...document,
      connectors: [...document.connectors, createConnector("moving", "X3", 1, { x: 1_500, y: 200 })],
      wires: document.wires.map((wire) => wire.id === "w2"
        ? { ...wire, e4Route: needlessHump, e4RouteMode: "auto" as const }
        : wire),
    };

    document = applyEditorCommand(document, {
      type: "move-connector", connectorId: "moving", view: "e4", position: { x: 1_500, y: 0 },
    });

    const route = document.wires.find((wire) => wire.id === "w2")!.e4Route;
    expect(route).not.toEqual(needlessHump);
    expect(route).toEqual([]);
  });

  it("removes a stale automatic hump after a neighboring connection is deleted", () => {
    let document = connectionDocument();
    const needlessHump = [
      { x: 648, y: 88 }, { x: 700, y: 88 }, { x: 700, y: 200 },
      { x: 900, y: 200 }, { x: 900, y: 88 }, { x: 976, y: 88 },
    ];
    document = {
      ...document,
      wires: document.wires.map((wire) => wire.id === "w2"
        ? { ...wire, e4Route: needlessHump, e4RouteMode: "auto" as const }
        : wire),
    };

    document = applyEditorCommand(document, { type: "remove-wire", wireId: "w1" });

    const remaining = document.wires.find((wire) => wire.id === "w2")!;
    expect(remaining.e4Route).toEqual([]);
    expect(remaining.e4RouteMode).toBe("auto");
  });

  it("stores, moves segments and repairs E4 routes after connector movement", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, {
      type: "set-e4-wire-route", wireId: "w1",
      route: [{ x: 648, y: 64 }, { x: 740, y: 64 }, { x: 740, y: 120 }, { x: 900, y: 120 }, { x: 900, y: 64 }, { x: 976, y: 64 }],
    });
    document = applyEditorCommand(document, {
      type: "move-e4-wire-segment", wireId: "w1", segmentIndex: 2, position: { x: 760, y: 0 },
    });
    expect(document.wires.find((wire) => wire.id === "w1")?.e4Route.slice(1, 3)).toEqual([
      { x: 760, y: 64 }, { x: 760, y: 120 },
    ]);
    document = applyEditorCommand(document, {
      type: "move-connector", connectorId: "x1", view: "e4", position: { x: 0, y: 200 },
    });
    const wire = document.wires.find((item) => item.id === "w1")!;
    expect(() => validateOrthogonalE4Route(
      { position: connectorContactPosition(document.connectors[0]!, "x1:contact:1", "e4")!, leadDirection: "right" },
      wire.e4Route,
      { position: connectorContactPosition(document.connectors[1]!, "x2:contact:1", "e4")!, leadDirection: "left" },
    )).not.toThrow();
  });

  it("keeps an unrelated automatic wire fixed while one segment is dragged", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, {
      type: "set-e4-wire-route", wireId: "w1",
      route: [{ x: 648, y: 64 }, { x: 740, y: 64 }, { x: 740, y: 120 }, { x: 900, y: 120 }, { x: 900, y: 64 }, { x: 976, y: 64 }],
    });
    const neighbourBefore = document.wires.find((wire) => wire.id === "w2")!;
    const moved = applyEditorCommand(document, {
      type: "move-e4-wire-segment", wireId: "w1", segmentIndex: 2, position: { x: 760, y: 0 },
    });
    expect(moved.wires.find((wire) => wire.id === "w2")?.e4Route).toEqual(neighbourBefore.e4Route);
    expect(moved.wires.find((wire) => wire.id === "w2")?.e4RouteMode).toBe(neighbourBefore.e4RouteMode);
  });

  it("detaches only geometric clearance and then optimizes the selected wire with its neighbours", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, { type: "set-e4-wire-route", wireId: "w1",
      route: [{ x: 648, y: 64 }, { x: 740, y: 64 }, { x: 740, y: 160 },
        { x: 900, y: 160 }, { x: 900, y: 64 }, { x: 976, y: 64 }] });
    // A manually pinned neighbour must not prevent a temporary drag.
    document = { ...document, wires: document.wires.map(wire => ({ ...wire, e4RouteMode: "manual" as const })) };
    const endpoints = document.wires.map(wire => [wire.from, wire.to]);
    const moved = applyEditorCommand(document, { type: "move-e4-wire-segment", wireId: "w1",
      segmentIndex: 3, position: { x: 0, y: 88 }, detached: true });
    expect(moved.wires[0]!.e4Route[2]!.y).toBe(88);
    expect(moved.wires.map(wire => [wire.from, wire.to])).toEqual(endpoints);
    expect(e4RoutingIssues(moved).length).toBeGreaterThan(0);
    const repaired = applyEditorCommand(moved, { type: "reroute-e4-wires", wireIds: ["w1", "w2"] });
    expect(e4RoutingIssues(repaired)).toEqual([]);
    expect(repaired.wires[0]!.e4Route).toEqual([]);
    expect(repaired.wires.map(wire => [wire.from, wire.to])).toEqual(endpoints);
  });

  it("preserves manual E4 guide geometry when a connected connector moves", () => {
    let document = singleWireConnectionDocument();
    const manualRoute = [
      { x: 648, y: 64 }, { x: 700, y: 64 }, { x: 700, y: 200 },
      { x: 900, y: 200 }, { x: 900, y: 64 }, { x: 976, y: 64 },
    ];
    document = applyEditorCommand(document, {
      type: "set-e4-wire-route", wireId: "w1", route: manualRoute,
    });

    document = applyEditorCommand(document, {
      type: "move-connector", connectorId: "x1", view: "e4", position: { x: 0, y: -16 },
    });

    const wire = document.wires[0]!;
    expect(wire.e4RouteMode).toBe("manual");
    expect(wire.e4Route).toEqual(expect.arrayContaining([
      { x: 700, y: 200 }, { x: 900, y: 200 },
    ]));
    expect(wire.e4Route.some((point) => point.x === 700)).toBe(true);
    expect(wire.e4Route.some((point) => point.x === 900)).toBe(true);
    expect(() => validateOrthogonalE4Route(
      { position: connectorContactPosition(document.connectors[0]!, "x1:contact:1", "e4")!, leadDirection: "right" },
      wire.e4Route,
      { position: connectorContactPosition(document.connectors[1]!, "x2:contact:1", "e4")!, leadDirection: "left" },
    )).not.toThrow();
  });

  it("removes a manual E4 bend and rejects an invalid route-point index", () => {
    let document = singleWireConnectionDocument();
    const manualRoute = [
      { x: 648, y: 64 }, { x: 700, y: 64 }, { x: 700, y: 200 },
      { x: 900, y: 200 }, { x: 900, y: 64 }, { x: 976, y: 64 },
    ];
    document = applyEditorCommand(document, {
      type: "set-e4-wire-route", wireId: "w1", route: manualRoute,
    });

    document = applyEditorCommand(document, {
      type: "remove-e4-wire-route-point", wireId: "w1", pointIndex: 2,
    });

    const wire = document.wires[0]!;
    expect(wire.e4RouteMode).toBe("manual");
    expect(wire.e4Route.length).toBeLessThan(manualRoute.length);
    expect(wire.e4Route).not.toContainEqual({ x: 700, y: 200 });
    expect(() => validateOrthogonalE4Route(
      { position: connectorContactPosition(document.connectors[0]!, "x1:contact:1", "e4")!, leadDirection: "right" },
      wire.e4Route,
      { position: connectorContactPosition(document.connectors[1]!, "x2:contact:1", "e4")!, leadDirection: "left" },
    )).not.toThrow();
    expect(() => applyEditorCommand(document, {
      type: "remove-e4-wire-route-point", wireId: "w1", pointIndex: 99,
    })).toThrow(/Точка маршрута Э4 не найдена/);
  });

  it("keeps drawn and electrical screen ports identical while sliding along inclined carriers",()=>{
    const base=connectionDocument();
    const inclined={...base,wires:base.wires.map((w,i)=>({...w,e4Route:[{x:700,y:64+24*i},{x:820,y:220+24*i},{x:940,y:220+24*i},{x:940,y:64+24*i}]}))};
    const d=applyEditorCommand(inclined,{type:"create-screen",screen:{id:"s",wireIds:["w1","w2"],position:.5,width:30,label:"S",terminalSide:"both"}});
    let diagonalPositions=0;
    for(let i=0;i<=20;i++){
      const moved=applyEditorCommand(d,{type:"update-screen",screenId:"s",position:i/20});
      const model=wireScreenConnectionGeometry(moved,"s")!;
      const drawing=getE4ScreenLayout(moved.screens[0]!,designToScene(moved,"e4"))!;
      expect(drawing.center).toEqual(model.center);
      expect(drawing.terminals).toEqual(model.terminals);
      expect(model.orientation).toBe("horizontal");
      if(model.center.x>700&&model.center.x<820){
        diagonalPositions++;
        expect(model.center.y).toBeCloseTo(76+(model.center.x-700)*156/120);
        expect(model.crossSize).toBeCloseTo(42);
      }
      expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(moved))).screens).toEqual(moved.screens);
      expect(moved.wires).toEqual(d.wires);
    }
    expect(diagonalPositions).toBeGreaterThan(1);
  });

  it("inserts an E4 midpoint with one command, persists it and undoes it",()=>{
    const d=singleWireConnectionDocument(),a=wireEndpointE4Anchor(d,d.wires[0]!.from)!.position,b=wireEndpointE4Anchor(d,d.wires[0]!.to)!.position;
    const point={x:(a.x+b.x)/2,y:a.y};
    const h=executeEditorCommand(createEditorHistory(d),{type:"edit-e4-bend",wireId:"w1",index:0,position:point,mode:"adjacent",insert:true});
    expect(h.present.wires[0]!.e4Route).toContainEqual(point);
    expect(h.present.wires[0]!.from).toEqual(d.wires[0]!.from);
    expect(h.present.wires[0]!.to).toEqual(d.wires[0]!.to);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(h.present))).wires[0]!.e4Route).toEqual(h.present.wires[0]!.e4Route);
    expect(undoEditorCommand(h).present).toBe(d);
  });

  it("preserves a screened pair and follows the screen lead after editing a carrier midpoint",()=>{
    let d=applyEditorCommand(connectionDocument(),{type:"create-screen",screen:{id:"edit-screen",wireIds:["w1","w2"],position:.5,width:46,label:"SH"}});
    d=applyEditorCommand(d,{type:"add-wire",wire:createWire("edit-lead",{connectorId:"x1",contactId:"x1:contact:3"},createScreenEndpoint("edit-screen"),100,"SHIELD")});
    const w=d.wires.find(w=>w.id==="w1")!,points=[wireEndpointE4Anchor(d,w.from)!.position,...w.e4Route,wireEndpointE4Anchor(d,w.to)!.position];
    const index=points.slice(1).map((p,i)=>({i,length:Math.hypot(p.x-points[i]!.x,p.y-points[i]!.y)})).sort((a,b)=>b.length-a.length)[0]!.i,a=points[index]!,b=points[index+1]!;
    const next=applyEditorCommand(d,{type:"edit-e4-bend",wireId:w.id,index,position:{x:(a.x+b.x)/2,y:(a.y+b.y)/2},mode:"adjacent",insert:true});
    expect(next.screens).toEqual(d.screens);
    expect(wireScreenConnectionGeometry(next,"edit-screen")).not.toBeNull();
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(next))).screens).toEqual(d.screens);
  });
  it("keeps a collinear editing handle inside the required contact lead",()=>{
    const base=singleWireConnectionDocument();
    const d=applyEditorCommand(base,{type:"set-e4-wire-route",wireId:"w1",route:[{x:648,y:64},{x:648,y:200},{x:976,y:200},{x:976,y:64}]});
    const point={x:636,y:64};
    const next=applyEditorCommand(d,{type:"edit-e4-bend",wireId:"w1",index:0,position:point,mode:"adjacent",insert:true});
    expect(next.wires[0]!.e4Route).toEqual([point,...d.wires[0]!.e4Route]);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(next))).wires[0]!.e4Route).toEqual(next.wires[0]!.e4Route);
    expect(e4RoutingIssues(next)).toEqual([]);
  });

  it("moves an E4 corner with Shift while preserving remote waypoints and rejects locked edits",()=>{
    const d=applyEditorCommand(singleWireConnectionDocument(),{type:"set-e4-wire-route",wireId:"w1",route:[{x:700,y:64},{x:700,y:200},{x:900,y:200},{x:900,y:64}]});
    const cmd={type:"edit-e4-bend" as const,wireId:"w1",index:1,position:{x:740,y:240},mode:"adjacent" as const};
    const moved=applyEditorCommand(d,cmd);
    expect(moved.wires[0]!.e4Route).toEqual([{x:700,y:64},{x:740,y:240},{x:900,y:200},{x:900,y:64}]);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(moved))).wires[0]!.e4Route).toEqual(moved.wires[0]!.e4Route);
    for(const p of [{x:700,y:64},{x:740,y:240},{x:900,y:200},{x:900,y:64}])expect(moved.wires[0]!.e4Route).toContainEqual(p);
    expect(e4RoutingIssues(moved)).toEqual([]);
    const locked={...d,views:{...d.views,e4:{...d.views.e4,layers:d.views.e4.layers.map(l=>({...l,locked:l.id===d.wires[0]!.layerIds.e4}))}}};
    expect(()=>applyEditorCommand(locked,cmd)).toThrow("заблокирован");
    expect(()=>applyEditorCommand(d,{...cmd,index:999})).toThrow("не найдена");
  });

  it("moves E4 connectors in carry/Shift modes without rerouting remote corners",()=>{
    const d=applyEditorCommand(singleWireConnectionDocument(),{type:"set-e4-wire-route",wireId:"w1",route:[{x:700,y:64},{x:700,y:200},{x:900,y:200},{x:900,y:64}]});
    const carry=applyEditorCommand(d,{type:"move-connector",connectorId:"x1",view:"e4",position:{x:20,y:30},physicalDragMode:"carry"});
    expect(carry.wires[0]!.e4Route).toEqual([{x:720,y:94},{x:700,y:200},{x:900,y:200},{x:900,y:64}]);
    const adjacent=applyEditorCommand(d,{type:"move-connector",connectorId:"x1",view:"e4",position:{x:20,y:30},physicalDragMode:"adjacent"});
    expect(adjacent.wires[0]!.e4Route.slice(1)).toEqual(d.wires[0]!.e4Route);
    for(const doc of [carry,adjacent]){
      expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(doc))).wires[0]!.e4Route).toEqual(doc.wires[0]!.e4Route);
      expect(doc.wires[0]!.from).toEqual(d.wires[0]!.from);expect(doc.wires[0]!.to).toEqual(d.wires[0]!.to);
    }
  });

  it("carries both E4 shoulders without doubling back along the contact lead",()=>{
    const d=applyEditorCommand(singleWireConnectionDocument(),{type:"set-e4-wire-route",wireId:"w1",route:[{x:700,y:64},{x:700,y:200},{x:800,y:200},{x:900,y:200},{x:900,y:64}]});
    const moved=applyEditorCommand(d,{type:"edit-e4-bend",wireId:"w1",index:2,position:{x:820,y:230},mode:"carry"});
    expect(moved.wires[0]!.e4Route).toContainEqual({x:720,y:230});
    expect(moved.wires[0]!.e4Route).toContainEqual({x:920,y:230});
    expect(e4RoutingIssues(moved)).toEqual([]);
  });

  it("moves junctions attached to a dragged internal segment and reroutes their branches", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, {
      type: "set-e4-wire-route", wireId: "w1",
      route: [{ x: 648, y: 64 }, { x: 740, y: 64 }, { x: 740, y: 120 }, { x: 900, y: 120 }, { x: 900, y: 64 }, { x: 976, y: 64 }],
    });
    document = applyEditorCommand(document, {
      type: "create-junction",
      junction: { id: "j1", position: { x: 800, y: 120 }, wireIds: ["w1", "w3"] },
      branchWire: {
        ...createWire("w3", { connectorId: "x1", contactId: "x1:contact:3" }, createJunctionEndpoint("j1"), 100, "NET-A"),
        e4Route: [{ x: 648, y: 112 }, { x: 800, y: 112 }],
      },
    });

    document = applyEditorCommand(document, {
      type: "move-e4-wire-segment", wireId: "w1", segmentIndex: 3, position: { x: 0, y: 160 },
    });

    expect(document.junctions[0]?.position).toEqual({ x: 800, y: 160 });
    expect(wireE4PathContainsPoint(document, document.wires.find((wire) => wire.id === "w1")!, { x: 800, y: 160 })).toBe(true);
    expect(wireE4PathContainsPoint(document, document.wires.find((wire) => wire.id === "w3")!, { x: 800, y: 160 })).toBe(true);
  });

  it("keeps an electrical branch attached while a free E4 shoulder moves, saves and undoes",()=>{
    let d=applyEditorCommand(connectionDocument(),{type:"set-e4-wire-route",wireId:"w1",route:[{x:648,y:64},{x:740,y:64},{x:740,y:180},{x:900,y:180},{x:900,y:64},{x:976,y:64}]});
    d=applyEditorCommand(d,{type:"create-junction",junction:{id:"j",position:{x:800,y:180},wireIds:["w1","branch"]},branchWire:{...createWire("branch",{connectorId:"x1",contactId:"x1:contact:3"},createJunctionEndpoint("j"),100,"NET-A"),e4Route:[{x:648,y:112},{x:800,y:112}]}});
    const original=d.wires.find(w=>w.id==="w1")!,index=original.e4Route.findIndex(p=>p.x===740&&p.y===180);
    const h=executeEditorCommand(createEditorHistory(d),{type:"edit-e4-bend",wireId:"w1",index,position:{x:760,y:220},mode:"adjacent"});
    const moved=h.present,j=moved.junctions[0]!;
    expect(j.position).toEqual({x:812.5,y:205});
    for(const id of j.wireIds)expect(wireE4PathContainsPoint(moved,moved.wires.find(w=>w.id===id)!,j.position)).toBe(true);
    expect(moved.wires.map(w=>[w.id,w.from,w.to])).toEqual(d.wires.map(w=>[w.id,w.from,w.to]));
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(moved))).junctions).toEqual(moved.junctions);
    expect(undoEditorCommand(h).present).toBe(d);
  });

  it.each(["carry","adjacent"] as const)("moves both a T carrier and its branch from the same connector in %s mode",mode=>{
    const base=connectionDocument();
    const d=applyEditorCommand(base,{type:"create-junction",junction:{id:"j",position:{x:700,y:64},wireIds:["w1","branch"]},branchWire:createWire("branch",{connectorId:"x1",contactId:"x1:contact:3"},createJunctionEndpoint("j"),100,"NET-A")});
    const history=executeEditorCommand(createEditorHistory(d),{type:"move-connector",connectorId:"x1",view:"e4",position:{x:10,y:20},physicalDragMode:mode});
    const changed=history.present,j=changed.junctions[0]!;
    expect(changed.connectors[0]!.positions.e4).toEqual({x:10,y:20});
    for(const id of j.wireIds)expect(wireE4PathContainsPoint(changed,changed.wires.find(w=>w.id===id)!,j.position)).toBe(true);
    expect(e4RoutingIssues(changed)).toEqual([]);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(changed))).junctions).toEqual(changed.junctions);
    expect(changed.wires.map(w=>[w.id,w.from,w.to])).toEqual(d.wires.map(w=>[w.id,w.from,w.to]));
    expect(undoEditorCommand(history).present).toBe(d);
  });

  it("reconciles two moving through-wires at one junction instead of rejecting their different projections",()=>{
    const base=connectionDocument();
    const d=parseHarnessDesignDocument({...base,wires:base.wires.map(w=>({...w,circuit:"NET-A",e4Route:w.id==="w1"
      ?[{x:740,y:64},{x:740,y:250},{x:860,y:250},{x:860,y:64}]
      :[{x:700,y:88},{x:800,y:188},{x:930,y:188},{x:930,y:88}]})),junctions:[{id:"j",position:{x:740,y:128},wireIds:["w1","w2"]}]});
    expect(e4RoutingIssues(d)).toEqual([]);
    const changed=applyEditorCommand(d,{type:"move-connector",connectorId:"x1",view:"e4",position:{x:10,y:20},physicalDragMode:"carry"});
    const j=changed.junctions[0]!;
    expect(j.position.x).toBeGreaterThan(740);
    for(const wire of changed.wires)expect(wireE4PathContainsPoint(changed,wire,j.position)).toBe(true);
    expect(e4RoutingIssues(changed)).toEqual([]);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(changed))).junctions).toEqual(changed.junctions);
  });

  it("preserves remote author corners of a valid branch while its carrier moves",()=>{
    let d=applyEditorCommand(connectionDocument(),{type:"set-e4-wire-route",wireId:"w1",route:[{x:740,y:64},{x:740,y:180},{x:900,y:180},{x:900,y:64}]});
    d=applyEditorCommand(d,{type:"create-junction",junction:{id:"j",position:{x:800,y:180},wireIds:["w1","branch"]},branchWire:createWire("branch",{connectorId:"x1",contactId:"x1:contact:3"},createJunctionEndpoint("j"),100,"NET-A")});
    d=applyEditorCommand(d,{type:"set-e4-wire-route",wireId:"branch",route:[{x:700,y:112},{x:700,y:400},{x:800,y:400}]});
    const index=d.wires[0]!.e4Route.findIndex(p=>p.x===900&&p.y===180);
    const changed=applyEditorCommand(d,{type:"edit-e4-bend",wireId:"w1",index,position:{x:920,y:200},mode:"adjacent"});
    const branch=changed.wires.find(w=>w.id==="branch")!;
    expect(branch.e4Route).toContainEqual({x:700,y:400});
    expect(branch.e4Route).toContainEqual({x:700,y:112});
    expect(e4RoutingIssues(changed)).toEqual([]);
  });

  it("rebuilds connected E4 wires after flipping a connector", () => {
    let document = connectionDocument();
    const before = document.wires.find((wire) => wire.id === "w1")!.e4Route;
    document = applyEditorCommand(document, { type: "flip-connector-orientation", connectorId: "x1" });
    const wire = document.wires.find((item) => item.id === "w1")!;
    expect(wire.e4Route).not.toEqual(before);
    expect(() => validateOrthogonalE4Route(
      { position: connectorContactPosition(document.connectors[0]!, "x1:contact:1", "e4")!, leadDirection: "left" },
      wire.e4Route,
      { position: connectorContactPosition(document.connectors[1]!, "x2:contact:1", "e4")!, leadDirection: "left" },
    )).not.toThrow();
  });

  it("rebuilds connected E4 wires after table width and contact row changes", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, {
      type: "add-custom-field", connectorId: "x1", field: { id: "note", label: "Примечание", visible: true },
    });
    document = applyEditorCommand(document, {
      type: "toggle-base-column-visibility", connectorId: "x1", key: "color",
    });
    let wire = document.wires.find((item) => item.id === "w2")!;
    expect(() => validateOrthogonalE4Route(
      { position: connectorContactPosition(document.connectors[0]!, "x1:contact:2", "e4")!, leadDirection: "right" },
      wire.e4Route,
      { position: connectorContactPosition(document.connectors[1]!, "x2:contact:2", "e4")!, leadDirection: "left" },
    )).not.toThrow();

    document = applyEditorCommand(document, { type: "remove-wire", wireId: "w1" });
    document = applyEditorCommand(document, { type: "remove-contact", connectorId: "x1", contactId: "x1:contact:1" });
    wire = document.wires.find((item) => item.id === "w2")!;
    expect(connectorContactPosition(document.connectors[0]!, "x1:contact:2", "e4")?.y).toBe(64);
    expect(() => validateOrthogonalE4Route(
      { position: connectorContactPosition(document.connectors[0]!, "x1:contact:2", "e4")!, leadDirection: "right" },
      wire.e4Route,
      { position: connectorContactPosition(document.connectors[1]!, "x2:contact:2", "e4")!, leadDirection: "left" },
    )).not.toThrow();
  });

  it("manages crossing style, differential pairs and screens and cleans them on wire removal", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, { type: "set-wire-crossing-style", view: "e4", style: "bridge" });
    document = applyEditorCommand(document, {
      type: "update-layer", view: "e4", layerId: "wires", visible: false,
    });
    document = applyEditorCommand(document, {
      type: "create-diff-pair", group: { id: "dp1", wireIds: ["w1", "w2"], step: 12, amplitude: 4, variant: 1 },
    });
    document = applyEditorCommand(document, {
      type: "update-diff-pair", groupId: "dp1", variant: 2, step: 16,
    });
    document = applyEditorCommand(document, {
      type: "create-screen", screen: { id: "s1", wireIds: ["w1", "w2"], position: 0.4, label: "SH1", width: 30 },
    });
    document = applyEditorCommand(document, {
      type: "update-screen", screenId: "s1", position: 0.6, label: "SH-A", terminalSide: "both",
    });
    expect(document.views.e4.wireCrossingStyle).toBe("bridge");
    expect(document.diffPairs[0]).toMatchObject({ variant: 2, step: 16 });
    expect(document.screens[0]).toMatchObject({ position: 0.6, label: "SH-A", terminalSide: "both" });
    document = applyEditorCommand(document, { type: "remove-wire", wireId: "w1" });
    expect(document.diffPairs).toHaveLength(0);
    expect(document.screens[0]?.wireIds).toEqual(["w2"]);
  });

  it("rejects differential-pair and screen groups that have no visible common parallel span", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, {
      type: "add-connector", connector: createConnector("x3", "X3", 1, { x: 2000, y: 300 }),
    });
    document = applyEditorCommand(document, {
      type: "add-connector", connector: createConnector("x4", "X4", 1, { x: 3000, y: 300 }),
    });
    document = applyEditorCommand(document, { type: "flip-connector-orientation", connectorId: "x4" });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w3", { connectorId: "x3", contactId: "x3:contact:1" }, { connectorId: "x4", contactId: "x4:contact:1" }),
    });

    expect(() => applyEditorCommand(document, {
      type: "create-diff-pair", group: { id: "dp-hidden", wireIds: ["w1", "w3"], step: 12, amplitude: 4, variant: 1 },
    })).toThrow(/общего параллельного участка/);
    expect(() => applyEditorCommand(document, {
      type: "create-screen", screen: { id: "s-hidden", wireIds: ["w1", "w3"], position: 0.5, label: "SH", width: 30 },
    })).toThrow(/общего поперечного охвата/);
  });

  it("connects an ordinary wire to the conducting point of a screen", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, {
      type: "create-screen",
      screen: { id: "s1", wireIds: ["w1", "w2"], position: 0.5, label: "SH1", width: 46 },
    });
    const geometry = wireScreenConnectionGeometry(document, "s1")!;
    expect(geometry.orientation).toBe("horizontal");
    expect(geometry.crossSize).toBeGreaterThan(geometry.alongSize);
    expect(geometry.connectionPoint).toEqual(geometry.bodyConnectionPoint);
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire(
        "shield-lead",
        { connectorId: "x1", contactId: "x1:contact:3" },
        createScreenEndpoint("s1"),
        100,
        "SHIELD",
      ),
    });
    expect(document.wires.find((wire) => wire.id === "shield-lead")?.to).toEqual({
      screenId: "s1", connectorId: "", contactId: "",
    });
    expect(wireEndpointE4Anchor(document, createScreenEndpoint("s1"))).toMatchObject({
      position: wireScreenConnectionGeometry(document, "s1")!.connectionPoint,
      leadDirection: "up",
    });
  });

  it("reroutes a screen connection when the screen or a screened manual segment moves", () => {
    let document = connectionDocument();
    const leadX = connectorContactPosition(document.connectors[0]!, "x1:contact:1", "e4")!.x + 24;
    document = applyEditorCommand(document, {
      type: "set-e4-wire-route",
      wireId: "w1",
      route: [
        { x: leadX, y: 64 }, { x: leadX, y: 16 }, { x: 800, y: 16 },
        { x: 800, y: 64 }, { x: 976, y: 64 },
      ],
    });
    document = applyEditorCommand(document, {
      type: "create-screen",
      screen: { id: "s1", wireIds: ["w1", "w2"], position: 0.35, label: "SH1", width: 46 },
    });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire(
        "shield-lead",
        { connectorId: "x1", contactId: "x1:contact:3" },
        createScreenEndpoint("s1"),
        100,
        "SHIELD",
      ),
    });

    const initialPoint = wireScreenConnectionGeometry(document, "s1")!.connectionPoint;
    const initialRoute = document.wires.find((wire) => wire.id === "shield-lead")!.e4Route;
    document = applyEditorCommand(document, {
      type: "update-screen", screenId: "s1", position: 0.7,
    });
    const movedPoint = wireScreenConnectionGeometry(document, "s1")!.connectionPoint;
    const movedRoute = document.wires.find((wire) => wire.id === "shield-lead")!.e4Route;
    expect(movedPoint).not.toEqual(initialPoint);
    expect(movedRoute).not.toEqual(initialRoute);

    document = applyEditorCommand(document, {
      // At 70% the screen is on the long final horizontal span of w1/w2. Move
      // the matching internal span of w2 so its bundle center, screen terminal
      // and attached lead route must all follow.
      type: "move-e4-wire-segment", wireId: "w1", segmentIndex: 3, position: { x: 840, y: 0 },
    });
    const finalPoint = wireScreenConnectionGeometry(document, "s1")!.connectionPoint;
    const lead = document.wires.find((wire) => wire.id === "shield-lead")!;
    const start = wireEndpointE4Anchor(document, lead.from)!;
    const end = wireEndpointE4Anchor(document, lead.to)!;
    expect(finalPoint).not.toEqual(movedPoint);
    expect(end.position).toEqual(finalPoint);
    expect(() => validateOrthogonalE4Route(start, lead.e4Route, end)).not.toThrow();
  });

  it("parses and removes a persisted screen endpoint without breaking legacy documents", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, {
      type: "create-screen",
      screen: { id: "s1", wireIds: ["w1"], position: 0.5, label: "SH1", width: 32 },
    });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("shield-lead", { connectorId: "x1", contactId: "x1:contact:3" }, createScreenEndpoint("s1")),
    });
    const parsed = parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)));
    expect(parsed.wires.find((wire) => wire.id === "shield-lead")?.to).toEqual({
      screenId: "s1", connectorId: "", contactId: "",
    });
    const removed = applyEditorCommand(parsed, { type: "remove-screen", screenId: "s1" });
    expect(removed.wires.some((wire) => wire.id === "shield-lead")).toBe(false);
  });

  it("keeps legacy screen documents above and persists an explicitly selected side", () => {
    const legacy = connectionDocument();
    const legacyJson = JSON.parse(JSON.stringify({
      ...legacy,
      screens: [{ id: "s1", wireIds: ["w1", "w2"], position: 0.5, label: "SH1", width: 46 }],
    })) as Record<string, unknown>;
    const parsedLegacy = parseHarnessDesignDocument(legacyJson);
    expect(parsedLegacy.screens[0]?.terminalSide).toBe("above");

    let document = applyEditorCommand(connectionDocument(), {
      type: "create-screen",
      screen: { id: "s2", wireIds: ["w1", "w2"], position: 0.5, label: "SH2", width: 46, terminalSide: "both" },
    });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire(
        "shield-lead-below",
        { connectorId: "x1", contactId: "x1:contact:3" },
        createScreenEndpoint("s2", "below"),
      ),
    });
    const restored = parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)));
    expect(restored.screens[0]?.terminalSide).toBe("both");
    expect(restored.wires.find((wire) => wire.id === "shield-lead-below")?.to).toMatchObject({
      screenId: "s2", screenTerminalSide: "below",
    });
    expect(wireEndpointE4Anchor(restored, createScreenEndpoint("s2", "below"))?.leadDirection).toBe("down");
  });

  it("keeps the two explicit screen ports distinct and never substitutes the opposite side", () => {
    let document = applyEditorCommand(connectionDocument(), {
      type: "create-screen",
      screen: { id: "s1", wireIds: ["w1", "w2"], position: 0.5, label: "SH1", width: 46, terminalSide: "both" },
    });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire(
        "screen-through",
        createScreenEndpoint("s1", "above"),
        createScreenEndpoint("s1", "below"),
      ),
    });
    expect(document.wires.find((wire) => wire.id === "screen-through")?.from).toMatchObject({
      screenId: "s1", screenTerminalSide: "above",
    });
    expect(document.wires.find((wire) => wire.id === "screen-through")?.to).toMatchObject({
      screenId: "s1", screenTerminalSide: "below",
    });
    expect(wireEndpointE4Anchor(document, createScreenEndpoint("s1", "above"))?.position)
      .not.toEqual(wireEndpointE4Anchor(document, createScreenEndpoint("s1", "below"))?.position);
    expect(() => applyEditorCommand(document, {
      type: "update-screen", screenId: "s1", terminalSide: "above",
    })).toThrow(/сторона экрана уже используется/i);
  });

  it("normalizes legacy routes whose old eight-unit clearance is no longer valid", () => {
    let legacy = connectionDocument();
    legacy = applyEditorCommand(legacy, {
      type: "add-wire",
      wire: createWire("w3", { connectorId: "x1", contactId: "x1:contact:3" },
        { connectorId: "x2", contactId: "x2:contact:3" }, 100, "NET-C"),
    });
    legacy = applyEditorCommand(legacy, {
      type: "add-wire",
      wire: createWire("w4", { connectorId: "x1", contactId: "x1:contact:4" },
        { connectorId: "x2", contactId: "x2:contact:4" }, 100, "NET-D"),
    });
    const withLegacySpacing = {
      ...legacy,
      wires: legacy.wires.map((wire) => ({
        ...wire,
        e4RouteMode: "manual" as const,
        e4Route: wire.id === "w1"
          ? [{ x: 648, y: 64 }, { x: 680, y: 64 }, { x: 680, y: 80 },
            { x: 944, y: 80 }, { x: 944, y: 64 }, { x: 976, y: 64 }]
          : wire.id === "w2"
            ? [{ x: 648, y: 88 }, { x: 976, y: 88 }]
            : wire.id === "w3"
              ? [{ x: 648, y: 112 }, { x: 680, y: 112 }, { x: 680, y: 88 },
                { x: 944, y: 88 }, { x: 944, y: 112 }, { x: 976, y: 112 }]
              : [{ x: 648, y: 136 }, { x: 976, y: 136 }],
      })),
    };
    withLegacySpacing.wires.forEach((wire) => expect(() => validateOrthogonalE4Route(
      wireEndpointE4Anchor(withLegacySpacing, wire.from)!,
      wire.e4Route,
      wireEndpointE4Anchor(withLegacySpacing, wire.to)!,
    )).not.toThrow());
    const fullPoints = (wireId: string) => {
      const wire = withLegacySpacing.wires.find((item) => item.id === wireId)!;
      return [wireEndpointE4Anchor(withLegacySpacing, wire.from)!.position,
        ...wire.e4Route, wireEndpointE4Anchor(withLegacySpacing, wire.to)!.position];
    };
    const validationRequest = (wireId: string, occupiedWireId: string) => {
      const wire = withLegacySpacing.wires.find((item) => item.id === wireId)!;
      return {
        start: wireEndpointE4Anchor(withLegacySpacing, wire.from)!,
        end: wireEndpointE4Anchor(withLegacySpacing, wire.to)!,
        occupiedRoutes: [{ id: occupiedWireId, points: fullPoints(occupiedWireId) }],
      };
    };
    expect(() => validateE4Route(fullPoints("w1"), validationRequest("w1", "w2"))).toThrow(/зазор/);
    expect(() => validateE4Route(fullPoints("w3"), validationRequest("w3", "w2"))).toThrow(/накладывается/);

    const preservedRoute = withLegacySpacing.wires.find((wire) => wire.id === "w4")!.e4Route;
    const normalized = normalizeE4RoutingDocument(withLegacySpacing);
    expect(normalized.wires.find((wire) => wire.id === "w4")?.e4Route).toEqual(preservedRoute);
    expect(normalized.wires.filter((wire) => wire.id !== "w4").map((wire) => wire.e4Route))
      .not.toEqual(withLegacySpacing.wires.filter((wire) => wire.id !== "w4").map((wire) => wire.e4Route));
    for (const wire of normalized.wires) {
      const start = wireEndpointE4Anchor(normalized, wire.from)!;
      const end = wireEndpointE4Anchor(normalized, wire.to)!;
      expect(() => validateOrthogonalE4Route(start, wire.e4Route, end)).not.toThrow();
      expect(() => validateE4Route(
        [start.position, ...wire.e4Route, end.position],
        {
          start,
          end,
          occupiedRoutes: normalized.wires.filter((item) => item.id !== wire.id).map((item) => ({
            id: item.id,
            points: [wireEndpointE4Anchor(normalized, item.from)!.position,
              ...item.e4Route, wireEndpointE4Anchor(normalized, item.to)!.position],
          })),
        },
      )).not.toThrow();
    }
  });

  it("defaults legacy differential pair variant to the first visual style", () => {
    const current = connectionDocument();
    const parsed = parseHarnessDesignDocument({
      ...current,
      diffPairs: [{ id: "dp-legacy", wireIds: ["w1", "w2"], step: 10, amplitude: 3 }],
    });
    expect(parsed.diffPairs[0]?.variant).toBe(1);
    expect(() => parseHarnessDesignDocument({
      ...current,
      diffPairs: [{ id: "dp-bad", wireIds: ["w1", "w2"], step: 10, amplitude: 3, variant: 3 }],
    })).toThrow(/Вид дифференциальной пары/);
  });

  it("creates an atomic T junction without splitting the target wire and unifies its circuit", () => {
    let document = connectionDocument();
    const branch = {
      ...createWire("w3", { connectorId: "x1", contactId: "x1:contact:3" }, createJunctionEndpoint("j1"), 100, ""),
      e4Route: [{ x: 648, y: 112 }, { x: 700, y: 112 }],
    };
    document = applyEditorCommand(document, {
      type: "create-junction",
      junction: { id: "j1", position: { x: 700, y: 64 }, wireIds: ["w1", "w3"] },
      branchWire: branch,
    });
    expect(document.wires.find((wire) => wire.id === "w1")?.to).toEqual({ connectorId: "x2", contactId: "x2:contact:1" });
    expect(document.wires.find((wire) => wire.id === "w3")?.circuit).toBe("NET-A");
    document = applyEditorCommand(document, { type: "move-junction", junctionId: "j1", position: { x: 740, y: 120 } });
    const trunk = document.wires.find((wire) => wire.id === "w1")!;
    expect(wireE4PathContainsPoint(document, trunk, { x: 740, y: 120 })).toBe(true);
    expect(trunk.e4Route).toHaveLength(4);
    expect(Math.min(...trunk.e4Route.map((point) => point.y))).toBe(64);
    expect(() => applyEditorCommand(document, { type: "remove-junction", junctionId: "j1" })).toThrow(/ветвь/);
  });

  it("atomically reconnects a wire endpoint to a point on another wire", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w3", { connectorId: "x1", contactId: "x1:contact:3" }, { connectorId: "x2", contactId: "x2:contact:3" }, 100, "NET-A"),
    });
    document = applyEditorCommand(document, {
      type: "connect-wire-to-wire", wireId: "w3", end: "to", targetWireId: "w1", junctionId: "j1", position: { x: 700, y: 64 },
    });
    expect(document.junctions[0]).toMatchObject({ id: "j1", wireIds: ["w1", "w3"] });
    expect(document.wires.find((wire) => wire.id === "w3")?.to).toMatchObject({ junctionId: "j1" });
    expect(() => applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w4", { connectorId: "x1", contactId: "x1:contact:2" }, createJunctionEndpoint("j1"), 100, ""),
    })).toThrow(/накладывается/);
    expect(document.junctions[0]?.wireIds).toEqual(["w1", "w3"]);
    expect(() => applyEditorCommand(document, {
      type: "connect-wire-to-wire", wireId: "w2", end: "to", targetWireId: "w1", junctionId: "j2", position: { x: 750, y: 64 },
    })).toThrow(/разными непустыми/);
  });

  it("rejects a second wire laid directly over an existing wire at a junction", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w3", { connectorId: "x1", contactId: "x1:contact:3" }, { connectorId: "x2", contactId: "x2:contact:3" }, 100, "NET-A"),
    });
    document = applyEditorCommand(document, {
      type: "connect-wire-to-wire", wireId: "w3", end: "to", targetWireId: "w1", junctionId: "j1", position: { x: 700, y: 64 },
    });
    expect(() => applyEditorCommand(document, {
      type: "add-wire",
      wire: {
        ...createWire("w4", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }, 100, "NET-A"),
        e4Route: document.wires.find((wire) => wire.id === "w1")!.e4Route,
      },
    })).toThrow(/накладывается/);
    expect(document.junctions.find((junction) => junction.id === "j1")?.wireIds).toEqual(["w1", "w3"]);
  });

  it("does not add a branch to a selected target that overlaps an existing wire", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w3", { connectorId: "x1", contactId: "x1:contact:3" }, { connectorId: "x2", contactId: "x2:contact:3" }, 100, "NET-A"),
    });
    document = applyEditorCommand(document, {
      type: "connect-wire-to-wire", wireId: "w3", end: "to", targetWireId: "w1", junctionId: "j1", position: { x: 700, y: 64 },
    });
    expect(() => applyEditorCommand(document, {
      type: "add-wire",
      wire: {
        ...createWire("w4", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }, 100, "NET-A"),
        e4Route: document.wires.find((wire) => wire.id === "w1")!.e4Route,
      },
    })).toThrow(/накладывается/);
    expect(document.junctions.find((junction) => junction.id === "j1")?.wireIds).toEqual(["w1", "w3"]);
  });

  it("reroutes connector and junction endpoint changes as valid E4 paths", () => {
    let document = connectionDocument();
    document = applyEditorCommand(document, {
      type: "reconnect-wire", wireId: "w1", end: "to", endpoint: { connectorId: "x2", contactId: "x2:contact:3" },
    });
    let wire = document.wires.find((item) => item.id === "w1")!;
    expect(() => validateOrthogonalE4Route(
      { position: connectorContactPosition(document.connectors[0]!, "x1:contact:1", "e4")!, leadDirection: "right" },
      wire.e4Route,
      { position: connectorContactPosition(document.connectors[1]!, "x2:contact:3", "e4")!, leadDirection: "left" },
    )).not.toThrow();

    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w3", { connectorId: "x1", contactId: "x1:contact:3" }, { connectorId: "x2", contactId: "x2:contact:4" }, 100, "NET-A"),
    });
    const junctionPoint = (() => {
      const target = document.wires.find((item) => item.id === "w1")!;
      const points = [
        connectorContactPosition(document.connectors[0]!, "x1:contact:1", "e4")!,
        ...target.e4Route,
        connectorContactPosition(document.connectors[1]!, "x2:contact:3", "e4")!,
      ];
      const first = points[0]!;
      const second = points[1]!;
      return first.y === second.y
        ? { x: (first.x + second.x) / 2, y: first.y }
        : { x: first.x, y: (first.y + second.y) / 2 };
    })();
    document = applyEditorCommand(document, {
      type: "connect-wire-to-wire", wireId: "w3", end: "to", targetWireId: "w1", junctionId: "j1", position: junctionPoint,
    });
    wire = document.wires.find((item) => item.id === "w3")!;
    expect(() => validateOrthogonalE4Route(
      { position: connectorContactPosition(document.connectors[0]!, "x1:contact:3", "e4")!, leadDirection: "right" },
      wire.e4Route,
      { position: junctionPoint, leadDirection: null },
    )).not.toThrow();
  });

  it("parses schema version 1 connection documents with safe defaults", () => {
    const current = connectionDocument();
    const legacy = {
      schemaVersion: 1,
      connectors: current.connectors,
      wires: current.wires.map(({ e4Route: _route, ...wire }) => wire),
      views: {
        e4: { layers: current.views.e4.layers },
        drawing: { layers: current.views.drawing.layers },
      },
    };
    const parsed = parseHarnessDesignDocument(legacy);
    expect(parsed.wires.every((wire) => wire.e4Route.length >= 2)).toBe(true);
    const parsedFirst = parsed.wires[0]!;
    expect(() => validateOrthogonalE4Route(
      { position: connectorContactPosition(parsed.connectors[0]!, "x1:contact:1", "e4")!, leadDirection: "right" },
      parsedFirst.e4Route,
      { position: connectorContactPosition(parsed.connectors[1]!, "x2:contact:1", "e4")!, leadDirection: "left" },
    )).not.toThrow();
    expect(parsed.junctions).toEqual([]);
    expect(parsed.diffPairs).toEqual([]);
    expect(parsed.screens).toEqual([]);
    expect(parsed.views.e4.wireCrossingStyle).toBe("none");
  });
});

function connectionDocument() {
  let document = createEmptyHarnessDesign();
  document = applyEditorCommand(document, {
    type: "add-connector", connector: createConnector("x1", "X1", 4, { x: 0, y: 0 }),
  });
  document = applyEditorCommand(document, {
    type: "add-connector", connector: createConnector("x2", "X2", 4, { x: 1000, y: 0 }),
  });
  document = applyEditorCommand(document, { type: "flip-connector-orientation", connectorId: "x2" });
  document = applyEditorCommand(document, {
    type: "add-wire", wire: createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }, 100, "NET-A"),
  });
  document = applyEditorCommand(document, {
    type: "add-wire", wire: createWire("w2", { connectorId: "x1", contactId: "x1:contact:2" }, { connectorId: "x2", contactId: "x2:contact:2" }, 100, "NET-B"),
  });
  document = applyEditorCommand(document, { type: "set-e4-wire-route", wireId: "w1", route: [{ x: 648, y: 64 }, { x: 976, y: 64 }] });
  document = applyEditorCommand(document, { type: "set-e4-wire-route", wireId: "w2", route: [{ x: 648, y: 88 }, { x: 976, y: 88 }] });
  return document;
}

function singleWireConnectionDocument() {
  let document = createEmptyHarnessDesign();
  document = applyEditorCommand(document, {
    type: "add-connector", connector: createConnector("x1", "X1", 1, { x: 0, y: 0 }),
  });
  document = applyEditorCommand(document, {
    type: "add-connector", connector: createConnector("x2", "X2", 1, { x: 1000, y: 0 }),
  });
  document = applyEditorCommand(document, { type: "flip-connector-orientation", connectorId: "x2" });
  return applyEditorCommand(document, {
    type: "add-wire",
    wire: createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }),
  });
}

it("C6 keeps a manually dragged E4 wire editable when a pinned neighbour owns its clearance",()=>{
 let document=connectionDocument();
 document=applyEditorCommand(document,{type:"set-e4-wire-route",wireId:"w1",route:[
  {x:648,y:64},{x:740,y:64},{x:740,y:160},{x:900,y:160},{x:900,y:64},{x:976,y:64},
 ]});
 const moved=applyEditorCommand(document,{type:"set-e4-wire-route",wireId:"w1",route:[
  {x:648,y:64},{x:740,y:64},{x:740,y:80},{x:900,y:80},{x:900,y:64},{x:976,y:64},
 ]});
 expect(moved.wires.find(wire=>wire.id==="w1")?.e4Route[2]?.y).not.toBe(80);
 expect(moved.wires.map(wire=>[wire.from,wire.to])).toEqual(document.wires.map(wire=>[wire.from,wire.to]));
 expect(e4RoutingIssues(moved)).toEqual([]);
});

it("C3 and C7 plan real automatic pair routes at 24 units for the full horizontal run",()=>{
 const left=createConnector("pair-left","XP1",2,{x:0,y:0});
 const rightBase=createConnector("pair-right","XP2",2,{x:1000,y:120});
 const right={...rightBase,schematic:{...rightBase.schematic,orientation:"contacts-left" as const}};
 const wires=[0,1].map(index=>createWire(`pair-${index}`,
  {connectorId:left.id,contactId:left.contacts[index]!.id},
  {connectorId:right.id,contactId:right.contacts[index]!.id}));
 let document: ReturnType<typeof createEmptyHarnessDesign>={...createEmptyHarnessDesign(),connectors:[left,right],wires:wires.map((wire,index)=>({...wire,e4Route:[{x:648,y:64+index*24},{x:976,y:64+index*24}]}))};
 document=applyEditorCommand(document,{type:"create-diff-pair",group:{id:"pair",wireIds:["pair-0","pair-1"],step:24,amplitude:4,variant:1}});
 const paths=document.wires.map(wire=>({id:wire.id,points:[wireEndpointE4Anchor(document,wire.from)!.position,...wire.e4Route,wireEndpointE4Anchor(document,wire.to)!.position]}));
 const span=commonHorizontalPairSpan(paths);
 expect(span).not.toBeNull();
 expect(span!.crossMaximum-span!.crossMinimum).toBe(24);
 expect(span!.end-span!.start).toBeGreaterThan(250);
 const layout=getE4DifferentialPairLayout({id:"pair",wireIds:["pair-0","pair-1"],step:24,amplitude:4,variant:1},designToScene(document,"e4"))!;
 expect(layout.span.orientation).toBe("horizontal");
 expect(layout.crossMaximum-layout.crossMinimum).toBe(24);
 expect(layout.span.start).toBe(span!.start);
 expect(layout.span.end).toBe(span!.end);
});

it("C5 adjusts one angular bend with both neighbouring directions on the 30 degree lattice",()=>{
 const base=singleWireConnectionDocument();
 const angular=applyEditorCommand(base,{type:"set-e4-routing-mode",mode:"angular"});
 const initial=applyEditorCommand(angular,{type:"set-e4-wire-route",wireId:"w1",route:[
  {x:700,y:64},{x:700,y:200},{x:900,y:200},{x:900,y:64},
 ]});
 const changed=applyEditorCommand(initial,{type:"edit-e4-bend",wireId:"w1",index:1,position:{x:740,y:235},mode:"adjacent"});
 const route=changed.wires[0]!.e4Route;
 const bend=route[1]!,previous=route[0]!,next=route[2]!;
 const onLattice=(a:{x:number;y:number},b:{x:number;y:number})=>{
  const turns=Math.atan2(b.y-a.y,b.x-a.x)/(Math.PI/6);
  return Math.abs(turns-Math.round(turns))<1e-6;
 };
 expect(onLattice(previous,bend)).toBe(true);
 expect(onLattice(bend,next)).toBe(true);
 expect(bend).not.toEqual(initial.wires[0]!.e4Route[1]);
 expect(e4RoutingIssues(changed)).toEqual([]);
});

it("C1 rejects a direct diagonal E4 route while angle mode is off",()=>{
 const document=singleWireConnectionDocument();
 expect(()=>applyEditorCommand(document,{type:"set-e4-wire-route",wireId:"w1",route:[
  {x:700,y:64},{x:800,y:120},{x:900,y:64},
 ]})).toThrow(/ортогональным/);
 expect(document.wires[0]!.e4RouteMode).toBe("auto");
});

it("C5 rejects a direct route with an arbitrary angle even when 30 degree mode is on",()=>{
 const document=applyEditorCommand(singleWireConnectionDocument(),{type:"set-e4-routing-mode",mode:"angular"});
 expect(()=>applyEditorCommand(document,{type:"set-e4-wire-route",wireId:"w1",route:[
  {x:700,y:64},{x:800,y:120},{x:900,y:64},
 ]})).toThrow(/30°/);
});

describe("E4 drawing placement history",()=>{
  it("persists independent position/visibility and undoes each operation without moving the table",()=>{
    const connector=templateConnector(),document={...createEmptyHarnessDesign(),connectors:[connector]};
    const initial=createEditorHistory(document);
    const moved=executeEditorCommand(initial,{type:"set-drawing-placement",connectorId:connector.id,drawingId:"drawing-1",offset:{x:150,y:-40}});
    const hidden=executeEditorCommand(moved,{type:"set-drawing-placement",connectorId:connector.id,drawingId:"drawing-1",visible:false});
    const restored=parseHarnessDesignDocument(JSON.parse(JSON.stringify(hidden.present)));
    expect(restored.connectors[0]!.drawingPlacements).toEqual([{drawingId:"drawing-1",visible:false,offset:{x:150,y:-40}}]);
    expect(restored.connectors[0]!.positions).toEqual(connector.positions);
    expect(undoEditorCommand(hidden).present).toEqual(moved.present);
    expect(undoEditorCommand(moved).present).toEqual(document);
    expect(()=>parseHarnessDesignDocument({...document,connectors:[{...connector,drawingPlacements:[{drawingId:"a",visible:true,offset:{x:NaN,y:0}}]}]})).toThrow();
  });
});

describe("contact-side drawing view",()=>{
  it("creates a reserved independent placement without moving the connector and preserves it through history",()=>{
    const connector=templateConnector(),document={...createEmptyHarnessDesign(),connectors:[connector]};
    const history=createEditorHistory(document);
    const added=executeEditorCommand(history,{type:"set-contact-side-view",connectorId:connector.id});
    const moved=executeEditorCommand(added,{type:"set-contact-side-view",connectorId:connector.id,offset:{x:340,y:-90},scale:1.75,rotationDegrees:30});
    const placement=moved.present.connectors[0]!.drawingPlacements?.find(item=>item.drawingId==="view:contact-side");
    expect(placement).toEqual({drawingId:"view:contact-side",visible:true,offset:{x:340,y:-90},scale:1.75,rotationDegrees:30});
    expect(moved.present.connectors[0]!.positions).toEqual(connector.positions);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(moved.present))).connectors[0]!.drawingPlacements).toEqual([placement]);
    expect(undoEditorCommand(moved).present).toEqual(added.present);
    expect(undoEditorCommand(added).present).toEqual(document);
  });

  it("rejects invalid contact-side placement and removes only the reserved view",()=>{
    const connector=templateConnector(),document={...createEmptyHarnessDesign(),connectors:[{...connector,drawingPlacements:[{drawingId:"drawing-1",visible:true,offset:{x:5,y:7}}]}]};
    expect(()=>applyEditorCommand(document,{type:"set-contact-side-view",connectorId:connector.id,scale:20.1})).toThrow(/Масштаб/);
    expect(()=>applyEditorCommand(document,{type:"set-contact-side-view",connectorId:connector.id,offset:{x:NaN,y:0}})).toThrow(/положение/);
    const withView=applyEditorCommand(document,{type:"set-contact-side-view",connectorId:connector.id});
    const removed=applyEditorCommand(withView,{type:"set-contact-side-view",connectorId:connector.id,remove:true});
    expect(removed.connectors[0]!.drawingPlacements).toEqual([{drawingId:"drawing-1",visible:true,offset:{x:5,y:7}}]);
  });
});

it("C1 reroutes an automatic neighbour to admit the second pin without moving tables",()=>{
 const left=createConnector("l","XS1",2,{x:25,y:20});
 const rightBase=createConnector("r","XS2",4,{x:700,y:44});
 const right={...rightBase,schematic:{...rightBase.schematic,orientation:"contacts-left" as const}};
 let doc: ReturnType<typeof createEmptyHarnessDesign>={...createEmptyHarnessDesign(),connectors:[left,right]};
 const first=createWire("w1",{connectorId:left.id,contactId:left.contacts[0]!.id},{connectorId:right.id,contactId:right.contacts[0]!.id},null);
 doc=applyEditorCommand(doc,{type:"add-wire",wire:first});
 const before=doc.wires[0]!.e4Route;
 const second=createWire("w2",{connectorId:left.id,contactId:left.contacts[1]!.id},{connectorId:right.id,contactId:right.contacts[1]!.id},null);
 doc=applyEditorCommand(doc,{type:"add-wire",wire:second});
 expect(doc.wires).toHaveLength(2);expect(e4RoutingIssues(doc)).toEqual([]);
 expect(doc.connectors).toEqual([left,right]);expect(doc.wires[0]!.e4Route).not.toEqual(before);
});

it.each([15,90,22.5,-180])("preserves the drawing centre, IDs and other views at %s degrees and supports undo",angle=>{
 const connector=templateConnector(),doc={...createEmptyHarnessDesign(),connectors:[connector]};
 const origin=connector.positions.drawing,center={x:origin.x+30,y:origin.y+20};
 const history=createEditorHistory(doc);
 const rotated=executeEditorCommand(history,{type:"set-drawing-placement",connectorId:connector.id,drawingId:"view:drawing",rotationDegrees:angle,rotationCenter:center});
 const result=rotated.present.connectors[0]!,a=angle*Math.PI/180;
 expect(result.positions.drawing.x+30*Math.cos(a)-20*Math.sin(a)).toBeCloseTo(center.x);
 expect(result.positions.drawing.y+30*Math.sin(a)+20*Math.cos(a)).toBeCloseTo(center.y);
 expect(result.positions.e4).toEqual(connector.positions.e4);
 expect(result.contacts).toEqual(connector.contacts);expect(result.libraryBinding).toEqual(connector.libraryBinding);
 expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(rotated.present)))).toEqual({...rotated.present, connectors: rotated.present.connectors.map(c=>({...c,libraryBinding:{...c.libraryBinding,contactNumbering:"source-v1"}}))});
 expect(undoEditorCommand(rotated).present).toEqual(doc);
});
