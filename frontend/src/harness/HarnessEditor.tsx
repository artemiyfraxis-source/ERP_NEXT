import { useMemo } from "react";
import "./harness.css";
import { HarnessDesignEditor, type HarnessDesignEditorProps } from "./editor/HarnessDesignEditor";
import { HarnessDocumentsContext, serverDocumentsSource } from "./panels/documents-source";
import { usePhoneView } from "./phone-view";

/** Э4 + чертёж жгута. Все стили редактора действуют только внутри `.harness-root`. Окна документов чертежа берут строки
 *  с сервера ERP по проекту и позиции (`documentsSource` подменяет источник, например в тестах). На телефоне — только
 *  просмотр: поле с прокруткой и масштабом, без инструментов и панелей правки. */
export default function HarnessEditor(props: HarnessDesignEditorProps) {
  const { documentsSource, projectId, harnessId } = props;
  const phone = usePhoneView();
  const source = useMemo(() => documentsSource ?? serverDocumentsSource(projectId, harnessId), [documentsSource, projectId, harnessId]);
  return <div className={phone ? "harness-root harness-phone" : "harness-root"}>
    <HarnessDocumentsContext.Provider value={source}>
      <HarnessDesignEditor {...props} readOnly={props.readOnly || phone} phoneView={phone} />
    </HarnessDocumentsContext.Provider>
  </div>;
}
