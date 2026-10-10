import { useMemo } from "react";
import "./harness.css";
import { HarnessDesignEditor, type HarnessDesignEditorProps } from "./editor/HarnessDesignEditor";
import { HarnessDocumentsContext, serverDocumentsSource } from "./panels/documents-source";

/** Э4 + чертёж жгута. Все стили редактора действуют только внутри `.harness-root`. Окна документов чертежа берут строки
 *  с сервера ERP по проекту и позиции (`documentsSource` подменяет источник, например в тестах). */
export default function HarnessEditor(props: HarnessDesignEditorProps) {
  const { documentsSource, projectId, harnessId } = props;
  const source = useMemo(() => documentsSource ?? serverDocumentsSource(projectId, harnessId), [documentsSource, projectId, harnessId]);
  return <div className="harness-root">
    <HarnessDocumentsContext.Provider value={source}>
      <HarnessDesignEditor {...props} />
    </HarnessDocumentsContext.Provider>
  </div>;
}
