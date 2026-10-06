import type { EditorPoint } from "./editor-types";

/** World-space bounds and contact anchors used for transient table alignment. */
export interface E4ConnectorSnapTarget {
  readonly id: string;
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly contactSide: "left" | "right";
  /** Legacy first contact centre, in world coordinates. */
  readonly firstContactY?: number;
  /** All contact row centres, in world coordinates. Empty tables have no row guide. */
  readonly contactRowsY?: readonly number[];
}

export interface E4ConnectorSnapGuides {
  readonly vertical?: { readonly x: number; readonly fromY: number; readonly toY: number };
  readonly horizontal?: { readonly y: number; readonly fromX: number; readonly toX: number };
}

export interface E4ConnectorSnapResult {
  readonly position: EditorPoint;
  readonly guides: E4ConnectorSnapGuides;
}

/** An eight-screen-pixel magnetic zone, independent per axis and independent of zoom. */
export function snapE4ConnectorPosition(
  moving: E4ConnectorSnapTarget,
  targets: readonly E4ConnectorSnapTarget[],
  position: EditorPoint,
  zoom: number,
  thresholdPixels = 8,
): E4ConnectorSnapResult {
  const threshold = thresholdPixels / (Number.isFinite(zoom) && zoom > 0 ? zoom : 1);
  const contactSideX = (table: E4ConnectorSnapTarget, x: number) =>
    x + (table.contactSide === "right" ? table.width : 0);
  const proposedContactX = contactSideX(moving, position.x);
  // Only the first visible row of the dragged table is the moving reference.
  const firstRow = moving.contactRowsY?.[0] ?? moving.firstContactY;
  const proposedRows = firstRow === undefined ? [] : [position.y + firstRow - moving.y];
  let closestX: E4ConnectorSnapTarget | undefined;
  let closestY: { readonly target: E4ConnectorSnapTarget; readonly targetY: number; readonly movingY: number } | undefined;
  let xDistance = threshold;
  let yDistance = threshold;
  for (const target of targets) {
    if (target.id === moving.id) continue;
    const horizontalDistance = Math.abs(contactSideX(target, target.x) - proposedContactX);
    if (horizontalDistance <= xDistance && (horizontalDistance < xDistance || !closestX)) {
      closestX = target;
      xDistance = horizontalDistance;
    }
    const targetRows = target.contactRowsY?.length ? target.contactRowsY
      : target.firstContactY === undefined ? [] : [target.firstContactY];
    for (const targetY of targetRows) for (const proposedY of proposedRows) {
      const verticalDistance = Math.abs(targetY - proposedY);
      if (verticalDistance <= yDistance && (verticalDistance < yDistance || !closestY)) {
        closestY = { target, targetY, movingY: proposedY };
        yDistance = verticalDistance;
      }
    }
  }
  const snappedX = closestX
    ? position.x + contactSideX(closestX, closestX.x) - proposedContactX : position.x;
  const snappedY = closestY
    ? position.y + closestY.targetY - closestY.movingY
    : position.y;
  return {
    position: { x: snappedX, y: snappedY },
    guides: {
      ...(closestX ? { vertical: {
        x: contactSideX(closestX, closestX.x),
        fromY: Math.min(snappedY, closestX.y),
        toY: Math.max(snappedY + moving.height, closestX.y + closestX.height),
      } } : {}),
      ...(closestY ? { horizontal: {
        y: closestY.targetY,
        fromX: Math.min(snappedX, closestY.target.x),
        toX: Math.max(snappedX + moving.width, closestY.target.x + closestY.target.width),
      } } : {}),
    },
  };
}
