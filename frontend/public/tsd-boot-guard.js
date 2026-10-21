/* Стенд, опция warehouse.tsd: сторож запуска на экране ТСД (Roller_Buyer index.html). Если приложение не запустилось в
   браузере устройства (Chrome 70 на ТСД дал белый экран), показать почему и отправить ошибку на компьютер
   (/api/devices/client-error → client-errors.log в папке данных стенда). Обычный скрипт ES5 из своего файла — он
   выполняется и там, где сборка не разобралась, и проходит политику безопасности (script-src 'self'). Работает только на
   адресе ТСД (?section=tsd, /tsd); вне стенда адрес ответа — 404, отправка ничего не делает. */
(function () {
  var search = window.location.search || "";
  if (!/(^|[?&])section=tsd(&|$)/.test(search) && window.location.pathname !== "/tsd") return;
  var errors = [];
  var sent = 0;

  function csrf() {
    var match = /(?:^|;\s*)(?:__Host-erp_csrf|erp_csrf)=([^;]*)/.exec(document.cookie);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function report(text) {
    text = String(text);
    // The modern bundle stepping aside for the legacy one in a browser without import.meta.resolve: expected.
    if (/import\.meta\.resolve not supported/.test(text)) return;
    errors.push(text);
    if (sent >= 5) return;
    sent += 1;
    try {
      var xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/devices/client-error");
      xhr.setRequestHeader("Content-Type", "application/json");
      var token = csrf();
      if (token) xhr.setRequestHeader("X-CSRF-Token", token);
      xhr.send(JSON.stringify({ message: text.slice(0, 1000), url: window.location.href.slice(0, 400) }));
    } catch (error) {
      // nothing more to do: the page itself shows the text
    }
  }

  function show() {
    var root = document.getElementById("root");
    if (!root || root.childNodes.length || !errors.length) return;
    var box = document.createElement("div");
    box.className = "boot-guard";
    var title = document.createElement("b");
    title.textContent = "ERP не запустилась в этом браузере.";
    box.appendChild(title);
    box.appendChild(document.createElement("br"));
    box.appendChild(document.createTextNode("Ошибка отправлена на компьютер (Оборудование → ТСД)."));
    var details = document.createElement("small");
    details.textContent = errors.join(" | ") + " — " + navigator.userAgent;
    box.appendChild(details);
    root.appendChild(box);
  }

  window.addEventListener("error", function (event) {
    var where = event.filename ? " @ " + event.filename.split("/").pop() + ":" + event.lineno : "";
    report((event.message || "ошибка") + where);
    setTimeout(show, 300);
  });
  window.addEventListener("unhandledrejection", function (event) {
    var reason = event.reason;
    report("promise: " + (reason && reason.message ? reason.message : reason));
    setTimeout(show, 300);
  });
  // Nothing drawn after 15 s and no error seen — still say so instead of a blank page.
  setTimeout(function () {
    var root = document.getElementById("root");
    if (root && !root.childNodes.length) {
      report("пусто через 15 с");
      show();
    }
  }, 15000);
})();
