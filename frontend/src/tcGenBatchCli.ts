/*
 * Точка входа Node для пакетной генерации техкарт: JSON `BatchInput` на stdin → `{ items: BatchResult[] }` на stdout.
 * Собирается `npm run build:tcgen` в dist-tools/tcGenBatchCli.js; запускает backend `app.workflows.techcards_all`.
 */
import { runBatch, type BatchInput } from "./tcGenBatch";

type NodeProcess = {
  stdin: { setEncoding(encoding: string): void; on(event: string, listener: (chunk: string) => void): void };
  stdout: { write(text: string): void };
};

const node = (globalThis as unknown as { process: NodeProcess }).process;
let text = "";
node.stdin.setEncoding("utf8");
node.stdin.on("data", (chunk) => { text += chunk; });
node.stdin.on("end", () => { node.stdout.write(JSON.stringify(runBatch(JSON.parse(text) as BatchInput))); });
