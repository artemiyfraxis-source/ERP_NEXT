import "./harness.css";
import { HarnessDesignEditor, type HarnessDesignEditorProps } from "./editor/HarnessDesignEditor";

/** Э4 + чертёж жгута. Все стили редактора действуют только внутри `.harness-root`. */
export default function HarnessEditor(props: HarnessDesignEditorProps) {
  return <div className="harness-root">
    <HarnessDesignEditor {...props} />
  </div>;
}
