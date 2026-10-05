import { applyEditorCommand, type EditorCommand } from "./commands";
import type { HarnessDesignDocument } from "./model";

export interface EditorHistory {
  readonly past: readonly HarnessDesignDocument[];
  readonly present: HarnessDesignDocument;
  readonly future: readonly HarnessDesignDocument[];
}

export function createEditorHistory(document: HarnessDesignDocument): EditorHistory {
  return { past: [], present: document, future: [] };
}

export function executeEditorCommand(history: EditorHistory, command: EditorCommand): EditorHistory {
  const next = applyEditorCommand(history.present, command);
  return {
    past: [...history.past, history.present].slice(-100),
    present: next,
    future: [],
  };
}

export function undoEditorCommand(history: EditorHistory): EditorHistory {
  const previous = history.past.at(-1);
  if (!previous) return history;
  return {
    past: history.past.slice(0, -1),
    present: previous,
    future: [history.present, ...history.future],
  };
}

export function redoEditorCommand(history: EditorHistory): EditorHistory {
  const next = history.future[0];
  if (!next) return history;
  return {
    past: [...history.past, history.present].slice(-100),
    present: next,
    future: history.future.slice(1),
  };
}
