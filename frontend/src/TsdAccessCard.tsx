import { useEffect, useState } from "react";
import { devicesApi, type ClientError } from "./devicesApi";
import "./tsdAccess.css";

/** «Склад → Оборудование» → «ТСД»: адрес экрана ТСД (там, где открыт ERP) с QR для сканера, как войти, «Проверка
 *  сканера» и ошибки браузера устройства. */
export function TsdAccessCard() {
  const [scanner, setScanner] = useState<string[] | null>(null);
  const [errors, setErrors] = useState<ClientError[] | null>(null);
  const address = `${window.location.origin}/tsd`;

  function loadLogs() {
    devicesApi.scannerLog().then((answer) => setScanner(answer.lines), () => setScanner([]));
    devicesApi.clientErrors().then((answer) => setErrors(answer.errors), () => setErrors([]));
  }

  useEffect(loadLogs, []);

  return <section className="panel equipment-card tsd-access" aria-label="ТСД">
    <h2>ТСД</h2>
    <div className="tsd-access-qrs">
      <figure>
        <img src={devicesApi.qrUrl(address)} alt={`QR ${address}`} width={148} height={148} />
        <figcaption>
          <strong>{address}</strong>
          <span className="page-sub">Откройте адрес в браузере ТСД или отсканируйте QR в адресную строку.</span>
        </figcaption>
      </figure>
    </div>
    <p className="page-sub">Вход на ТСД — по PIN сотрудника («Сотрудники» → PIN). Кладовщику нужен раздел «Склад».</p>

    <div className="tsd-access-logs">
      <div>
        <h3>Проверка сканера</h3>
        <p className="page-sub">На ТСД: «Сканер не вводит код? Проверить сканер» — что сканер прислал, видно здесь.</p>
        <pre className="scanner-log tsd-access-log">{scanner === null ? "Загрузка…" : scanner.length ? scanner.join("\n") :"Проверок ещё не было."}</pre>
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
