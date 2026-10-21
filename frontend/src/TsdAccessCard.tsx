import { useEffect, useState } from "react";
import { failureText } from "./api";
import { devicesApi, TSD_OPTION, type ClientError, type DevicesStatus } from "./devicesApi";
import { useStageOption } from "./StageOptions";
import "./tsdAccess.css";

const POLL_MS = 15_000;

/** «Оборудование» → «ТСД и вход с устройств» (stage option warehouse.tsd; Roller_Buyer DeviceAccess, the device part):
 *  the switch that lets devices of the office Wi-Fi and Tailscale in (the administrator's), what is listening, the
 *  addresses with a QR for the ТСД's scanner, how to sign in there, the scanner check and the device browser's errors. */
export function TsdAccessCard({ admin }: { admin: boolean }) {
  const on = useStageOption(TSD_OPTION);
  const [status, setStatus] = useState<DevicesStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [scanner, setScanner] = useState<string[] | null>(null);
  const [errors, setErrors] = useState<ClientError[] | null>(null);

  function refresh() {
    devicesApi.status().then((next) => { setStatus(next); setError(""); }, (reason) => setError(failureText(reason, "Состояние не загрузилось")));
  }
  function loadLogs() {
    devicesApi.scannerLog().then((answer) => setScanner(answer.lines), () => setScanner([]));
    devicesApi.clientErrors().then((answer) => setErrors(answer.errors), () => setErrors([]));
  }

  useEffect(() => {
    if (!on) return;
    refresh();
    loadLogs();
    const timer = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer);
  }, [on]);

  if (!on) return null;

  async function switchNetwork(enabled: boolean) {
    setBusy(true);
    setError("");
    try {
      setStatus(await devicesApi.setNetwork(enabled));
    } catch (reason) {
      setError(failureText(reason, "Не переключилось"));
    } finally {
      setBusy(false);
    }
  }

  return <section className="panel equipment-card tsd-access" aria-label="ТСД и вход с устройств">
    <h2>ТСД и вход с устройств</h2>
    <p className="page-sub">Экран склада для терминала сбора данных (и телефона): скан ГРН и места, «Списать», «Вернуть»,
      «Переместить». Без переключателя ERP открывается только на этом компьютере.</p>
    {!status && !error && <p className="page-sub">Загрузка…</p>}
    {status && <>
      <label className="staff-switch tsd-access-switch">
        <input type="checkbox" role="switch" aria-label="Пускать устройства из сети (Wi-Fi офиса, Tailscale)" checked={status.enabled}
          disabled={!admin || busy} onChange={(event) => void switchNetwork(event.target.checked)} />
        <span>Пускать устройства из сети (Wi-Fi офиса, Tailscale)</span>
      </label>
      {!admin && <p className="page-sub">Переключает администратор.</p>}
      <p className={status.enabled && !status.listening ? "tsd-access-state warn" : "tsd-access-state"} role="status">
        {busy ? "Переключаю…" : !status.enabled ? "Сейчас ERP открывается только на этом компьютере."
          : status.listening ? `Устройства пускаются: порт ${status.port}, ${status.https ? "http:// и https://" : "только http://"}.`
            : `Устройства не пускаются: ${status.reason || "ворота не запущены"}`}
      </p>
      {status.listening && status.reason && <p className="page-sub">https:// не работает: {status.reason}</p>}
      {status.listening && <>
        <p>Откройте адрес в браузере ТСД — или отсканируйте QR сканером ТСД в адресную строку:</p>
        <div className="tsd-access-qrs">
          {status.addresses.map((address) => {
            const target = `${address.url}/tsd`;
            return <figure key={address.url}>
              <img src={devicesApi.qrUrl(target)} alt={`QR ${target}`} width={148} height={148} />
              <figcaption>
                <strong>{target}</strong>
                <span className="page-sub">{address.label === "Tailscale" ? "через Tailscale (нужно приложение на устройстве)" : "сеть офиса (Wi-Fi, кабель)"}</span>
              </figcaption>
            </figure>;
          })}
          {status.addresses.length === 0 && <p className="page-sub">У компьютера не нашлось адреса в сети.</p>}
        </div>
        {status.https && <p className="page-sub">Адрес работает и по http://, и по https://. Если браузер сам ушёл на https:// и пишет
          «Подключение не защищено» — это сертификат этого компьютера: «Дополнительные» → «Перейти на сайт», один раз на устройство.</p>}
      </>}
      <ul className="tsd-access-notes">
        <li>Вход на ТСД — по PIN сотрудника («Сотрудники» → PIN). Кладовщику нужен раздел «Склад».</li>
        <li>При первом включении Windows может спросить, пускать ли Python в сеть, — разрешите для частной сети.</li>
      </ul>
    </>}
    {error && <div className="error-banner" role="alert">{error}</div>}

    <div className="tsd-access-logs">
      <div>
        <h3>Проверка сканера</h3>
        <p className="page-sub">На ТСД: «Сканер не вводит код? Проверить сканер» — что сканер прислал, видно здесь.</p>
        <pre className="scanner-log tsd-access-log">{scanner === null ? "Загрузка…" : scanner.length ? scanner.join("\n") : "Проверок ещё не было."}</pre>
      </div>
      <div>
        <h3>Ошибки браузера устройства</h3>
        <p className="page-sub">Если ТСД показал белый экран, причина приходит сюда.</p>
        {errors === null ? <p className="page-sub">Загрузка…</p> : errors.length === 0 ? <p className="page-sub">Ошибок не было.</p>
          : <div className="table-wrap"><table className="grid compact" aria-label="Ошибки браузера устройства">
            <thead><tr><th>Когда</th><th>Адрес</th><th className="grow">Ошибка</th><th>Браузер</th></tr></thead>
            <tbody>{errors.map((item, index) => <tr key={`${item.at}-${index}`}>
              <td>{item.at}</td><td>{item.ip}</td><td className="text">{item.message}</td><td className="text">{item.browser}</td>
            </tr>)}</tbody>
          </table></div>}
      </div>
      <button type="button" className="secondary small" onClick={loadLogs}>Обновить</button>
    </div>
  </section>;
}
