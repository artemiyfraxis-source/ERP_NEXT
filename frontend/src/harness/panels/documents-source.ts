// Документы чертежа жгута — «Таблица соединений», «Карта резки», «Спецификация» — строит сервер
// (techcards/harness/documents.py, `kitsetsApi.harnessDocuments`). Окна редактора спрашивают его по текущему чертежу из
// редактора, поэтому правка видна сразу, без ожидания автосохранения; файл для Excel — по сохранённому чертежу.
import { createContext, useContext, useEffect, useRef, useState } from "react";
import "./panels.css";
import { failureText, kitsetsApi, type HarnessDocumentKind, type HarnessDocuments } from "../../api";
import type { HarnessDesignDocument } from "../editor/model";

export interface HarnessDocumentsSource {
  load<K extends HarnessDocumentKind>(kind: K, quantity: number, design: HarnessDesignDocument): Promise<HarnessDocuments[K]>;
  /** Скачать документ сохранённого чертежа для Excel. */
  download(kind: HarnessDocumentKind, quantity: number): void;
}

export function serverDocumentsSource(project: string, index: string): HarnessDocumentsSource {
  return {
    load: (kind, quantity, design) =>
      kitsetsApi.harnessDocuments(project, index, kind, quantity, design as unknown as Record<string, unknown>),
    download: (kind, quantity) => {
      const link = window.document.createElement("a");
      link.href = kitsetsApi.harnessDocumentsExcelUrl(project, index, kind, quantity);
      link.download = "";
      link.click();
    },
  };
}

export const HarnessDocumentsContext = createContext<HarnessDocumentsSource | null>(null);

export function useHarnessDocumentsSource(): HarnessDocumentsSource | null {
  return useContext(HarnessDocumentsContext);
}

export interface HarnessDocumentState<K extends HarnessDocumentKind> {
  /** Последний ответ сервера; пока идёт новый запрос, остаётся прежний (без мигания). */
  readonly data: HarnessDocuments[K] | null;
  readonly error: string;
  readonly loading: boolean;
}

/** Пауза после правки, прежде чем спросить сервер: набор текста в ячейке — один запрос, а не на каждую букву. */
export const DOCUMENT_DELAY_MS = 250;

export function useHarnessDocument<K extends HarnessDocumentKind>(kind: K, document: HarnessDesignDocument, quantity: number): HarnessDocumentState<K> {
  const source = useHarnessDocumentsSource();
  const [state, setState] = useState<HarnessDocumentState<K>>({ data: null, error: "", loading: true });
  const first = useRef(true);
  useEffect(() => {
    if (!source) {
      setState({ data: null, error: "Документы чертежа недоступны", loading: false });
      return;
    }
    let live = true;
    const run = () => {
      setState((current) => ({ ...current, loading: true }));
      source.load(kind, quantity, document)
        .then((data) => { if (live) setState({ data, error: "", loading: false }); })
        .catch((reason) => { if (live) setState((current) => ({ ...current, error: failureText(reason, "Документ не построен"), loading: false })); });
    };
    if (first.current) { first.current = false; run(); return () => { live = false; }; }
    const timer = setTimeout(run, DOCUMENT_DELAY_MS);
    return () => { live = false; clearTimeout(timer); };
  }, [source, kind, document, quantity]);
  return state;
}

/** Миллиметры и метры по-русски: «1 234,5». */
export function formatNumber(value: number | null, digits = 3): string {
  return value === null ? "—" : value.toLocaleString("ru-RU", { maximumFractionDigits: digits });
}
