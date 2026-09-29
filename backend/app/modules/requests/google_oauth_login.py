"""One-time sign-in that produces ``ERP_KD_DRIVE_OAUTH_JSON``: ``python -m app.modules.requests.google_oauth_login``.

The owner of the «!Проекты» folder runs it on their own computer with the OAuth client of the Google Cloud project
(type «Desktop app», its downloaded ``client_secret_….json``). A browser opens, the owner allows Drive access, and
the script writes an ``authorized_user`` JSON (client_id, client_secret, refresh_token, account) — the value of
``ERP_KD_DRIVE_OAUTH_JSON``. Loopback redirect + PKCE, pure ``httpx``, no new packages. The file is a secret:
it goes to the GitHub secret of the same name, never into git (README).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .google_drive import GOOGLE_TOKEN_URI, OAUTH_ENV_VAR, SCOPE

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
ABOUT_API = "https://www.googleapis.com/drive/v3/about"


def read_client(path: str) -> tuple[str, str]:
    info = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    block = info.get("installed") or info.get("web") or info
    client_id, client_secret = str(block.get("client_id") or ""), str(block.get("client_secret") or "")
    if not (client_id and client_secret):
        raise SystemExit(f"В {path} нет client_id/client_secret — скачайте JSON OAuth-клиента типа «Desktop app».")
    return client_id, client_secret


def redirect_server(state: str) -> tuple[dict[str, str], http.server.HTTPServer]:
    """A one-shot server on 127.0.0.1 (free port) that stores the redirect's query and stops."""
    received: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server API
            query = parse_qs(urlparse(self.path).query)
            received.update({key: values[0] for key, values in query.items()})
            ok = received.get("state") == state and "code" in received
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            text = "Готово, окно можно закрыть." if ok else "Вход не удался, смотрите терминал."
            self.wfile.write(f"<p>{text}</p>".encode("utf-8"))
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *args):
            pass

    return received, http.server.HTTPServer(("127.0.0.1", 0), Handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Получить {OAUTH_ENV_VAR} для записи КД на Диск.")
    parser.add_argument("--client", required=True, help="client_secret_….json OAuth-клиента «Desktop app»")
    parser.add_argument("--out", default="erp-kd-oauth.json", help="куда записать результат")
    args = parser.parse_args(argv)
    client_id, client_secret = read_client(args.client)

    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    received, server = redirect_server(state)
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}"
    url = AUTH_URI + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    print("Войдите аккаунтом-владельцем папки «!Проекты». Если браузер не открылся, откройте ссылку:\n" + url)
    webbrowser.open(url)
    server.serve_forever()
    server.server_close()
    if received.get("state") != state or "code" not in received:
        print(f"Google не вернул код: {received.get('error') or 'неверный ответ'}", file=sys.stderr)
        return 1

    token = httpx.post(GOOGLE_TOKEN_URI, data={
        "grant_type": "authorization_code", "code": received["code"], "redirect_uri": redirect_uri,
        "client_id": client_id, "client_secret": client_secret, "code_verifier": verifier,
    }, timeout=30.0)
    body = token.json() if token.status_code == 200 else {}
    refresh = str(body.get("refresh_token") or "")
    if not refresh:
        print(f"Google не выдал refresh_token (HTTP {token.status_code}).", file=sys.stderr)
        return 1
    about = httpx.get(ABOUT_API, params={"fields": "user(emailAddress)"},
                      headers={"Authorization": f"Bearer {body.get('access_token')}"}, timeout=30.0)
    account = str(((about.json() if about.status_code == 200 else {}).get("user") or {}).get("emailAddress") or "")
    result = {"type": "authorized_user", "client_id": client_id, "client_secret": client_secret,
              "refresh_token": refresh, "token_uri": GOOGLE_TOKEN_URI, "account": account}
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Записано в {args.out} ({account or 'аккаунт не определён'}). Содержимое файла — в секрет GitHub "
          f"{OAUTH_ENV_VAR}; сам файл в git не класть.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
