import { useEffect, useState, type FormEvent } from "react";
import { failureText, procurementApi, type ProcurementFile, type ProcurementFileKind, type ProcurementLine } from "./api";
import { canEditNote, saveLineField, type LineEditing } from "./ProcurementCellEdit";

/* Документы строки закупки — как колонки листа SUPPLYMAP DATASHEET, РАЗРЕШЕНИЕ и ДОК. КАЧЕСТВА: ссылка на описание
   позиции и файлы разрешения на замену и документа качества. Правит закупщик строки, в том же окне статусов, что и
   «Комментарий» (не «Новый» и не «Отменено»). Документ качества, приложенный файлом, ставит строке «Док. качества: Да» —
   приёмка тогда ждёт его при входном контроле (решено агентом — проверить). */

export function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

export const FILE_KIND_TITLE: Record<ProcurementFileKind, string> = { permit: "Файл разрешения", quality: "Документ качества" };

const WEB_LINK = /^https?:\/\/\S+$/i;

const filesOf =(line: ProcurementLine, kind: ProcurementFileKind): ProcurementFile[] =>
  (kind === "permit" ? line.permit_files : line.quality_files) ?? [];

/** Файлы одного вида ссылками, по одной в строку; имя файла целиком — в подсказке. */
export function FileLinks({ files }: { files: ProcurementFile[] }) {
  if (!files.length) return null;
  return (
    <span className="file-links">
      {files.map((file) => (
        <a key={file.id} href={file.url} target="_blank" rel="noreferrer" title={file.file_name}>{file.file_name}</a>
      ))}
    </span>
  );
}

/** Ячейка листа «Файлы разрешения» / «Док. качества»: ссылки на файлы или пусто. */
export function lineFilesColumn(kind: ProcurementFileKind) {
  return {
    value: (line: ProcurementLine) => filesOf(line, kind).map((file) => file.file_name).join(", "),
    render: (line: ProcurementLine) => <FileLinks files={filesOf(line, kind)} />,
  };
}

function FileInput({ line, kind, disabled, onSaved, extra }: {
  line: ProcurementLine;
  kind: ProcurementFileKind;
  disabled: boolean;
  onSaved: (line: ProcurementLine, file: ProcurementFile) => void;
  /** Дописка к заголовку (у документа качества — нужен ли он по заявке). */
  extra?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function upload(file: File) {
    setBusy(true);
    setError("");
    try {
      const result = await procurementApi.uploadLineFile(line.id, kind, {
        name: file.name, type: file.type || "application/octet-stream", base64: await readBase64(file),
      });
      onSaved(result.line, result.file);
    } catch (reason) {
      setError(failureText(reason, "Файл не загружен"));
    } finally {
      setBusy(false);
    }
  }

  const files = filesOf(line, kind);
  return (
    <div className="line-doc-field">
      <span className="line-doc-title">{FILE_KIND_TITLE[kind]}{files.length > 0 && ` · ${files.length}`}{extra}</span>
      <FileLinks files={files} />
      {!files.length && <span className="line-doc-empty">нет файла</span>}
      <label className={`secondary small file-button${disabled || busy ? " disabled" : ""}`}>
        {busy ? "Загружаем…" : files.length ? "Приложить ещё" : "Приложить файл"}
        <input
          type="file" hidden accept=".pdf,image/*" aria-label={`${FILE_KIND_TITLE[kind]} ${line.legacy_id}`} disabled={disabled || busy}
          onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void upload(file); }}
        />
      </label>
      {error && <small className="danger-text" role="alert">{error}</small>}
    </div>
  );
}

/** Карточка строки: DATASHEET и файлы разрешения и документа качества. */
export function LineDocumentsForm({ line, editing, disabled, onMessage }: {
  line: ProcurementLine;
  editing: LineEditing;
  disabled: boolean;
  onMessage?: (message: string) => void;
}) {
  const [draft, setDraft] = useState(line.datasheet ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setDraft(line.datasheet ?? ""); }, [line.version, line.datasheet]);
  const closed = !canEditNote(line);

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await saveLineField(line, "datasheet", draft.trim(), editing);
      onMessage?.(`${line.legacy_id}: DATASHEET сохранён`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не сохранилось");
    } finally {
      setSaving(false);
    }
  }

  function uploaded(saved: ProcurementLine, file: ProcurementFile) {
    editing.onSaved(saved);
    const flag = file.kind === "quality" && line.quality_document !== "Да" ? " · «Док. качества»: Да" : "";
    onMessage?.(`${line.legacy_id}: ${FILE_KIND_TITLE[file.kind].toLowerCase()} «${file.file_name}» приложен${flag}`);
  }

  const locked = disabled || closed;
  return (
    <form className="line-documents" aria-label={`Документы ${line.legacy_id}`} onSubmit={(event) => void save(event)}>
      <div className="form-grid">
        <label className="wide half">DATASHEET
          <span className="line-doc-datasheet">
            <input value={draft} placeholder="ссылка или текст" disabled={locked || saving} onChange={(event) => setDraft(event.target.value)} />
            {WEB_LINK.test((line.datasheet ?? "").trim()) && <a href={(line.datasheet ?? "").trim()} target="_blank" rel="noreferrer">открыть</a>}
          </span>
          {error && <small className="danger-text" role="alert">{error}</small>}
        </label>
        <FileInput line={line} kind="permit" disabled={locked} onSaved={uploaded} />
        <FileInput
          line={line} kind="quality" disabled={locked} onSaved={uploaded}
          extra={` · нужен: ${line.quality_document === "Да" ? "Да" : "Нет"}`}
        />
      </div>
      <div className="card-footer">
        <small>{closed ? "Строка закрыта для правки" : "Документ качества файлом ставит «Док. качества: Да»"}</small>
        <button className="secondary small" disabled={locked || saving || draft.trim() === (line.datasheet ?? "")}>
          {saving ? "Сохраняем…" : "Сохранить DATASHEET"}
        </button>
      </div>
    </form>
  );
}
