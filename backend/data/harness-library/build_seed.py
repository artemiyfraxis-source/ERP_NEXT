"""Builds `seed.json` — the first templates of the harness component library (migration 0091 inserts them).

JST SH housings SHR-02V-S … SHR-20V-S: TECHMAP-GRAPHER component templates (schemaVersion 3) drawn with Василий's
own JST SH artwork (`design/jst-sh-assets/variants/jst_sh_NN_contacts.png`, TECHMAP-GRAPHER © Василий,
SaveOurSouls, branch main b7b5cde, used with the author's permission; copied unchanged into `jst-sh/`). Not the QCAD
hatching catalog (GPL) — nothing of it is here.

Each template: one article (the housing), N contacts of the group «Контакт», a «Чертёж» view with the picture and a
contact point at every contact centre on the wire side (the picture's top edge, wires go up), an «Э4» view with a
box and a contact point per row on its right. Contact 1 is on the RIGHT as seen from the wire side with the ▽ mark
up — the ERP housing rule for SH (`techcards/connector_housings.py`, JST eSH drawing). Ids are uuid5 of the
connector key, so a rebuild gives the same templates.

Run from the repository root: `backend/.venv/Scripts/python.exe backend/data/harness-library/build_seed.py`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

HERE = Path(__file__).resolve().parent
PICTURES = HERE / "jst-sh"
SEED = HERE / "seed.json"

SOURCE_ID = "erp-connectors"
ENTITY_TYPE = "connector"
# Pixels of the artwork → template units of the drawing view (the artwork is 768 px tall, a pitch is 136 px).
SCALE = 0.1
PITCH_PX, FIRST_PX, HEIGHT_PX, WIRE_SIDE_PX = 136, 260, 768, 70
E4_ROW, E4_WIDTH = 20.0, 70.0


def _id(*parts: object) -> str:
    return str(uuid5(NAMESPACE_URL, "fraxis-erp:harness-library:" + ":".join(str(part) for part in parts)))


def _c(value: float) -> dict:
    return {"kind": "constant", "value": round(value, 4)}


def _transform() -> dict:
    return {"translateX": _c(0), "translateY": _c(0), "rotationDegrees": _c(0), "scaleX": _c(1), "scaleY": _c(1)}


def _node(ref: str, name: str, layer: str, kind: str, geometry: dict, *, stroke: str = "#27445a",
          width: float = 1.0) -> dict:
    return {"id": _id(ref, "node", name), "kind": kind, "layerId": layer, "visible": True, "locked": False,
            "opacity": 1, "transform": _transform(), "stroke": {"color": stroke, "width": _c(width)},
            "fill": {"color": None}, "geometry": geometry}


def template(count: int, picture: bytes, file_name: str) -> dict:
    ref = f"SHR-{count:02d}V-S"
    group = _id(ref, "group")
    contacts = [{"id": _id(ref, "contact", number), "number": str(number), "name": f"Контакт {number}",
                 "circuitText": None, "contactTypeGroupId": group} for number in range(1, count + 1)]
    asset = {"assetId": _id(ref, "asset"), "fileName": file_name, "mediaType": "image/png",
             "sha256": hashlib.sha256(picture).hexdigest(), "sizeBytes": len(picture)}
    width_px = 516 + PITCH_PX * (count - 1)

    drawing_layer = _id(ref, "layer", "drawing")
    image = _node(ref, "picture", drawing_layer, "image", {
        "assetId": asset["assetId"], "x": _c(0), "y": _c(0), "width": _c(width_px * SCALE),
        "height": _c(HEIGHT_PX * SCALE), "cropX": 0, "cropY": 0, "cropWidth": 1, "cropHeight": 1, "underlay": False,
    })
    # Contact 1 on the right: contact n sits at the (count + 1 − n)-th slot from the left.
    drawing_points = [{
        "id": _id(ref, "point", "drawing", contact["number"]), "logicalContactId": contact["id"],
        "x": _c((FIRST_PX + PITCH_PX * (count - int(contact["number"]))) * SCALE), "y": _c(WIRE_SIDE_PX * SCALE),
        "direction": "up",
    } for contact in contacts]

    e4_layer = _id(ref, "layer", "e4")
    box = _node(ref, "e4-box", e4_layer, "rectangle", {
        "x": _c(0), "y": _c(0), "width": _c(E4_WIDTH), "height": _c(E4_ROW * count),
        "cornerRadii": [_c(0), _c(0), _c(0), _c(0)],
    })
    title = _node(ref, "e4-title", e4_layer, "text", {"x": _c(6), "y": _c(-6), "text": ref, "fontSize": _c(10)})
    labels = [_node(ref, f"e4-label-{contact['number']}", e4_layer, "text", {
        "x": _c(E4_WIDTH - 18), "y": _c(E4_ROW * index + E4_ROW * 0.65), "text": contact["number"], "fontSize": _c(9),
    }) for index, contact in enumerate(contacts)]
    e4_points = [{
        "id": _id(ref, "point", "e4", contact["number"]), "logicalContactId": contact["id"],
        "x": _c(E4_WIDTH), "y": _c(E4_ROW * index + E4_ROW / 2), "direction": "right",
    } for index, contact in enumerate(contacts)]

    article = {"sourceId": SOURCE_ID, "entityType": ENTITY_TYPE, "articleKey": ref}
    content = {
        "schemaVersion": 3,
        "views": [
            {"id": _id(ref, "view", "e4"), "name": "Э4", "kind": "e4",
             "layers": [{"id": e4_layer, "name": "Основной", "visible": True, "locked": False,
                         "nodes": [box, title, *labels]}],
             "contactPoints": e4_points, "bundlePorts": [], "repeatPlacements": []},
            {"id": _id(ref, "view", "drawing"), "name": "Чертёж", "kind": "drawing",
             "layers": [{"id": drawing_layer, "name": "Основной", "visible": True, "locked": False,
                         "nodes": [image]}],
             "contactPoints": drawing_points, "bundlePorts": [], "repeatPlacements": []},
        ],
        "logicalContacts": contacts,
        "parameters": [], "repeaters": [], "assets": [asset],
        "contactTypeGroups": [{"id": group, "name": "Контакт"}],
        "articleVariants": [{"id": _id(ref, "article"), **article, "parameterValues": [], "contactGroups": None}],
    }
    return {
        "templateId": str(uuid5(NAMESPACE_URL, f"fraxis-erp:harness-component:{ref}")), "revision": 1,
        "connectorRef": ref, "code": ref, "name": f"Корпус JST SH, {count} конт., шаг 1,0 мм",
        "articleBindings": [article], "assets": [asset], "content": content, "picture": f"jst-sh/{file_name}",
    }


def main() -> None:
    templates = []
    for count in range(2, 21):
        file_name = f"jst_sh_{count:02d}_contacts.png"
        templates.append(template(count, (PICTURES / file_name).read_bytes(), file_name))
    seed = {
        "source": "TECHMAP-GRAPHER © Василий (SaveOurSouls), design/jst-sh-assets (main b7b5cde), with the author's "
                  "permission",
        "createdBy": "Василий (TECHMAP-GRAPHER)",
        "templates": templates,
    }
    head = json.dumps({key: value for key, value in seed.items() if key != "templates"}, ensure_ascii=False)[:-1]
    rows = ",\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in templates)
    SEED.write_text(f'{head}, "templates": [\n{rows}\n]}}\n', encoding="utf-8", newline="\n")
    print(f"{SEED.name}: {len(templates)} templates")


if __name__ == "__main__":
    main()
