// @vitest-environment node
import {expect,it} from "vitest";
import {physicalFixture} from "./physical-topology-fixture";
import {standardCovering,type CoveringMaterial} from "./physical-coverings";
import {coveringScene} from "./covering-layout";
import {buildDrawingBom,emptyDrawingDocuments} from "./drawing-documents";
import {emptyCoveringLibrary,textureChoices,validateCoveringLibrary,type CoveringLibrary} from "./covering-library";
import {withCoveringTextureUrls} from "./covering-assets";
import {parseHarnessDesignDocument} from "./model";
import {applyEditorCommand} from "./commands";
const hash="a".repeat(64),texture=`asset:${hash}` as const;
const material:CoveringMaterial={sourceId:"technology-database",snapshotId:"12345678-1234-4123-8123-123456789abc",snapshotSha256:"b".repeat(64),recordId:"c".repeat(64),sourceKey:"HS-1",displayName:"Термоусадка HS-1",entityType:"protective-covering"};
const library:CoveringLibrary={textures:[{sha256:hash,name:"Полосы"}],defaults:{"heat-shrink":{texture,material}}};
it("new sleeves inherit texture and pinned material while placed materials survive preference edits",()=>{
  const base=physicalFixture(),d={...base,drawingDocuments:{...emptyDrawingDocuments(),coveringLibrary:library}};
  const cover=standardCovering(d,"S0",{x:200,y:60},"Термоусадка","new-cover");
  expect(cover.style?.texture).toBe(texture);expect(cover.material).toEqual(material);
  const withCover={...d,physicalTopology:{...d.physicalTopology!,coverings:[cover]}};
  const changed=applyEditorCommand(withCover,{type:"set-drawing-documents",documents:{...d.drawingDocuments,coveringLibrary:emptyCoveringLibrary()}});
  expect(changed.physicalTopology!.coverings![0]).toEqual(cover);
  expect(buildDrawingBom(changed).some(row=>row.name===material.displayName)).toBe(true);
  expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(withCover))).drawingDocuments?.coveringLibrary).toEqual(library);
});
it("automatic textures follow the type preference and resolve against the current project after import",()=>{
  const d=physicalFixture(),cover=standardCovering(d,"S0",{x:200,y:60},"Термоусадка","cover");
  const scene=coveringScene({...d,drawingDocuments:{...emptyDrawingDocuments(),coveringLibrary:library},physicalTopology:{...d.physicalTopology!,coverings:[cover]}});
  expect(withCoveringTextureUrls(scene,{[hash]:"/project-copy/attachment-new/content"})[0]!.metadata!.coveringTextureUrl).toBe("/project-copy/attachment-new/content");
  expect(withCoveringTextureUrls(scene,{})[0]!.metadata!.coveringTextureUrl).toBe("");
  expect(textureChoices(library).find(t=>t.value===texture)?.label).toBe("Полосы");
});
it.each([null,[],{textures:[],defaults:null},{textures:[{sha256:hash,name:""}],defaults:{}},{textures:[{sha256:hash,name:"a"},{sha256:hash,name:"b"}],defaults:{}},{textures:[],defaults:{braid:{texture}}},{textures:[],defaults:{invalid:{texture:"auto"}}},{textures:[],defaults:{braid:{texture:"none",material:{}}}}])("rejects invalid material preferences %j",value=>expect(()=>validateCoveringLibrary(value)).toThrow());
