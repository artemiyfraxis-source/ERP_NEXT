import { useCallback, useEffect, useRef, useState } from "react";
import { failureText, vnaCaptureApi, type VnaAgent, type VnaCaptureFile } from "./api";
import { EMPTY, formatMoment, formatNumber } from "./format";
import type { MachineRow, Tone } from "./machinesApi";
import { openSection } from "./navigation";
import { PageWindow } from "./PageWindow";
import { VnaBench } from "./VnaBench";

/* Карточка анализатора цепей в «Оборудовании» (владелец 20.11.2026: «зачем ВАЦ в ОТК, если карточка оборудования в
   оборудовании и вся работа должна быть там»; «нужно делать связку и второго векторника»). Работа — по тому, что
   сообщает агент: Planar (S2VNA) — рабочее место ВАЦ целиком (`VnaBench`: калибровка АКМ, сеанс, проверки, экраны);
   Arinst (ArinstVL) — замер файлом: ArinstVL сохраняет Touchstone, агент отдаёт самый свежий за 15 минут
   (тот же путь заданий ВАЦ, что «Снять с ВАЦ» в замерах КСВН). «Подключение» — последним и свёрнутым. */

const sameNumber = (a: string, b: string) => a.replace(/\s+/g, "").toUpperCase() === b.replace(/\s+/g, "").toUpperCase();

/** Этим прибором управляет рабочее место ВАЦ: Planar, номер СИ которого назвал сервер рабочего места. */
export function drivesBench(row: MachineRow, benchNumber: string | null): boolean {
  return row.kind === "device" && row.vna === "s2vna" && Boolean(benchNumber) && sameNumber(row.inventory, benchNumber!);
}

const toneClass = (tone: Tone | undefined) => (tone && tone !== "neutral" ? `tone-${tone}` : "");

/** «Подключение»: паспорт прибора у агента — свёрнут внизу, строка-сводка видна всегда. Значения переносятся. */
export function AnalyserConnection({ row }: { row: MachineRow }) {
  const facts: [string, string, Tone?][] = [["Связь", row.link.text, row.link.tone], ["Состояние", row.state.text, row.state.tone],
    ["Устройство", row.device], ["Артикул", row.article], ["ПК", row.pc], ["IP", row.ip], ["Агент", row.service],
    ["Версия агента", row.version], ["Последний сигнал", row.last_seen ? formatMoment(row.last_seen) : ""], ["Также на ПК", row.others]];
  const summary = [row.pc && `ПК ${row.pc}`, row.version && `агент ${row.version}`].filter(Boolean).join(" · ");
  return <details className="machine-facts analyser-connection">
    <summary><b>Подключение</b> <span className="machine-muted">· связь с агентом:</span> <span className={toneClass(row.link.tone)}>{row.link.text}</span>
      {summary && <span className="machine-muted"> · {summary}</span>}</summary>
    <dl>{facts.map(([label, value, tone]) => <div key={label}>
      <dt>{label}</dt>
      <dd className={toneClass(tone)}>{value || EMPTY}</dd>
    </div>)}</dl>
  </details>;
}

/** Состояние Arinst словами — то, что агент сообщает: ArinstVL запущена или свежий файл за 15 минут; связь с ПК. */
export function arinstStatus(row: MachineRow): { text: string; tone: string } {
  const pc = row.pc || "ПК прибора";
  if (row.link.code !== "ok") return { text: `Нет связи с агентом на ${pc} (${row.link.text})`, tone: "tone-danger" };
  if (row.state.text === "Подключён") return { text: `Готов: на ${pc} запущена ArinstVL или сохранён свежий файл замера`, tone: "tone-ok" };
  if (row.state.text === "Установлена программа") {
    return { text: `На ${pc} установлена ArinstVL, прибор не подтверждён — агент ${row.version || ""} старый и видит только программу`, tone: "tone-warn" };
  }
  const since = row.last_seen ? `, последний раз ${formatMoment(row.last_seen)}` : "";
  return { text: `Не найден: на ${pc} ArinstVL не запущена (или не установлена) и свежих файлов замера за 15 минут нет${since}`,
    tone: "tone-warn" };
}

function band(file: VnaCaptureFile): string {
  const s = file.capture_settings;
  if (!s || s.start_hz == null || s.stop_hz == null) return EMPTY;
  return `${formatNumber(s.start_hz / 1e6, { digits: 0 })}–${formatNumber(s.stop_hz / 1e6, { digits: 0 })} МГц`;
}

async function download(file: VnaCaptureFile) {
  const blob = await vnaCaptureApi.file(file.id);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = file.file_name;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const pause = (signal: AbortSignal) => new Promise<void>((resolve) => {
  const timer = setTimeout(resolve, 1000);
  signal.addEventListener("abort", () => { clearTimeout(timer); resolve(); }, { once: true });
});

/** Arinst: состояние, «Забрать файл из ArinstVL» со своим результатом, последние снятые файлы. */
export function ArinstWork({ row, canQuality }: { row: MachineRow; canQuality: boolean }) {
  const status = arinstStatus(row);
  const instrumentId = row.instrument_id ?? "";
  const [files, setFiles] = useState<VnaCaptureFile[] | null>(null);
  const [listError, setListError] = useState("");
  const [result, setResult] = useState<{ text: string; tone: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [agents, setAgents] = useState<VnaAgent[] | null>(null);
  const run = useRef<AbortController | null>(null);
  const job = useRef("");

  const load = useCallback(async () => {
    if (!canQuality || !instrumentId) return;
    try { setFiles((await vnaCaptureApi.recent(instrumentId)).captures); setListError(""); }
    catch (reason) { setListError(failureText(reason, "Не удалось загрузить последние файлы")); }
  }, [canQuality, instrumentId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => () => run.current?.abort(), []);

  async function take(agentId = "") {
    if (busy || !instrumentId) return;
    const controller = new AbortController();
    run.current = controller;
    let done = false;
    setBusy(true); setAgents(null); setResult({ text: "Отправлено агенту — ждём файл…", tone: "machine-muted" });
    try {
      const answer = await vnaCaptureApi.start(instrumentId, agentId);
      if (answer.status === "choose") { setAgents(answer.agents); setResult({ text: "Arinst сообщают несколько ПК — выберите, с какого забрать файл", tone: "tone-warn" }); return; }
      if (answer.status === "no_agent") { setResult({ text: answer.message ?? "Агент с ArinstVL не на связи", tone: "tone-danger" }); return; }
      job.current = answer.id;
      let current = answer;
      const deadline = Date.now() + 120_000;
      while (!controller.signal.aborted) {
        if (current.status === "failed") throw new Error(current.error || "Агент не прислал файл");
        if (current.status === "done") {
          done = true;
          setResult({ text: `Получен ${formatMoment(new Date().toISOString())}: ${current.file_name}`, tone: "tone-ok" });
          await load();
          return;
        }
        if (Date.now() >= deadline) throw new Error("Агент не ответил за 2 минуты — проверьте ПК прибора");
        await pause(controller.signal);
        if (!controller.signal.aborted) current = await vnaCaptureApi.status(answer.id);
      }
    } catch (reason) {
      if (!controller.signal.aborted) setResult({ text: `Отказ: ${failureText(reason, "файл не получен")}`, tone: "tone-danger" });
    } finally {
      // Не дождались файла (отказ, отмена, закрыли карточку) — задание агенту снимаем, чтобы он не взял его позже.
      if (job.current && !done) void vnaCaptureApi.cancel(job.current).catch(() => undefined);
      job.current = "";
      if (run.current === controller) { run.current = null; if (!controller.signal.aborted) setBusy(false); }
    }
  }

  return <div className="analyser-arinst">
    <p className={`analyser-status ${status.tone}`} role="status">{status.text}</p>
    {!instrumentId ? <p className="warning-inline">Arinst нет в реестре СИ — укажите программу ВАЦ «Arinst VL» в карточке прибора (ОТК → Средства измерения), тогда файлы можно будет снимать.</p>
      : !canQuality ? <p className="machine-muted">Снимать файлы замера может сотрудник с «ОТК» на правку.</p>
        : <section className="machine-facts analyser-capture" aria-label="Замер файлом">
          <h3>Замер</h3>
          <ol className="analyser-steps">
            <li>Сохраните замер в ArinstVL — файл .s1p или .s2p в «Документы», «Рабочий стол» или «Загрузки».</li>
            <li>Нажмите «Забрать файл» — ERP возьмёт самый свежий файл за последние 15 минут.</li>
          </ol>
          <div className="analyser-command">
            <button type="button" className="primary" disabled={busy} onClick={() => void take()}>{busy ? "Забираю файл…" : "Забрать файл из ArinstVL"}</button>
            {busy && <button type="button" className="secondary" onClick={() => { run.current?.abort(); setBusy(false); setResult({ text: "Отменено", tone: "machine-muted" }); }}>Отменить</button>}
            {result && <span className={result.tone} role="status">{result.text}</span>}
          </div>
          {agents && <div className="analyser-command" role="group" aria-label="ПК с Arinst">
            {agents.map((agent) => <button key={agent.id} type="button" className="secondary" disabled={busy} onClick={() => void take(agent.id)}>С ПК {agent.pc}</button>)}
          </div>}
          <h3>Последние файлы</h3>
          {listError && <p className="error-banner" role="alert">{listError}</p>}
          {!files && !listError && <p className="empty">Загрузка…</p>}
          {files && !files.length && <p className="empty">Файлов ещё нет — сохраните замер в ArinstVL и нажмите «Забрать файл».</p>}
          {files && files.length > 0 && <div className="table-wrap"><table className="grid analyser-files" aria-label="Последние файлы Arinst">
            <thead><tr><th scope="col">Файл</th><th scope="col">Получен</th><th scope="col">ПК</th><th scope="col">Полоса</th><th scope="col">Точек</th><th scope="col" aria-label="Действия" /></tr></thead>
            <tbody>{files.map((file) => <tr key={file.id}>
              <td className="analyser-file-name">{file.file_name}</td>
              <td className="num">{formatMoment(file.finished_at) || EMPTY}</td>
              <td>{file.pc || EMPTY}</td>
              <td className="num">{band(file)}</td>
              <td className="num">{file.capture_settings?.points != null ? formatNumber(file.capture_settings.points) : EMPTY}</td>
              <td><button type="button" className="secondary small" aria-label={`Скачать ${file.file_name}`} onClick={() => void download(file).catch((reason) => setListError(failureText(reason, "Файл не скачан")))}>Скачать</button></td>
            </tr>)}</tbody>
          </table></div>}
          <div className="analyser-command">
            <span className="machine-muted">Замер КБ записывается в «Замерах КСВН»: «Снять с ВАЦ» в строке изделия берёт самый свежий файл ArinstVL.</span>
            <button type="button" className="secondary small" onClick={() => openSection("quality", { tab: "vswr" })}>Открыть замеры КСВН</button>
          </div>
        </section>}
  </div>;
}

/** Карточка анализатора: Planar рабочего места — `VnaBench`; Arinst — замер файлом; любой другой — паспорт.
 *  `row` null — рабочее место открыто ссылкой, а прибора в списке агентов нет. `benchNumber`: null — ещё проверяем,
 *  «» — рабочее место на сервере не отвечает. */
export function AnalyserCard({ row, benchNumber, canQuality, close }: {
  row: MachineRow | null; benchNumber: string | null; canQuality: boolean; close: () => void;
}) {
  const bench = row ? drivesBench(row, benchNumber) : Boolean(benchNumber);
  const planar = !row || row.vna === "s2vna";
  const title = row ? [row.inventory, row.name].filter(Boolean).join(" · ") : `${benchNumber ?? ""} · Анализатор цепей`;
  return <PageWindow label={title} close={close} width={planar ? 1280 : 960}>
    <section className="panel machine-card analyser-card">
      <header className="machine-card-head">
        <h2>{title}</h2>
        <button type="button" className="secondary small" onClick={close}>Закрыть</button>
      </header>
      {planar && (!canQuality ? <p className="machine-muted">Рабочее место ВАЦ открыто сотрудникам с «ОТК» на правку.</p>
        : bench ? <VnaBench canEdit={canQuality} />
          : benchNumber === null ? <p className="empty" role="status">Загрузка…</p>
            : benchNumber === "" ? <p className="warning-inline" role="status">Рабочее место ВАЦ на сервере сейчас не отвечает — обновите страницу позже.</p>
              : <p className="machine-muted">Рабочее место ВАЦ ведёт {benchNumber}; этим прибором оно не управляет.</p>)}
      {row?.vna === "arinst" && <ArinstWork row={row} canQuality={canQuality} />}
      {row && <AnalyserConnection row={row} />}
    </section>
  </PageWindow>;
}
