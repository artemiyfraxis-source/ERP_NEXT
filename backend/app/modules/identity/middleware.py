"""Resolve opaque server sessions and enforce CSRF on cookie-authenticated writes; resolve `Bearer erp_…` API tokens
into service principals (no cookies, no CSRF)."""

from collections.abc import Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.shared.clock import moscow_now

from .application import UnitOfWork, csrf_matches, is_api_token, resolve_api_token, resolve_session


SESSION_COOKIE = "erp_session"
CSRF_COOKIE = "erp_csrf"
HOST_SESSION_COOKIE = "__Host-erp_session"
HOST_CSRF_COOKIE = "__Host-erp_csrf"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def cookie_names(secure: bool) -> tuple[str, str]:
    if secure:
        return HOST_SESSION_COOKIE, HOST_CSRF_COOKIE
    return SESSION_COOKIE, CSRF_COOKIE


def security_cookie(request: Request, name: str) -> str:
    values = []
    for header in request.headers.getlist("cookie"):
        for item in header.split(";"):
            key, separator, value = item.strip().partition("=")
            if separator and key == name:
                values.append(value)
    return values[0] if len(values) == 1 else ""


class IdentitySessionMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        factory: Callable[[], UnitOfWork] | None = getattr(
            request.app.state,
            "identity_uow_factory",
            None,
        )
        clock = getattr(request.app.state, "identity_clock", moscow_now)
        scheme, _, credential = request.headers.get("authorization", "").partition(" ")
        if scheme.casefold() == "bearer":
            # Токен API: no cookies are read, so there is nothing to forge cross-site and no CSRF check. An unknown,
            # expired or revoked token is refused here; the token itself is never logged or echoed.
            credential = credential.strip()
            principal = None
            if callable(factory) and is_api_token(credential):
                principal = await run_in_threadpool(resolve_api_token, credential, clock(), factory())
            if principal is None:
                response = JSONResponse(
                    {"detail": "Токен API недействителен"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
            scope.setdefault("state", {})["principal"] = principal
            await self.app(scope, receive, send)
            return

        session_cookie, csrf_cookie_name = cookie_names(
            bool(getattr(request.app.state, "cookie_secure", True))
        )
        token = security_cookie(request, session_cookie)
        resolved = None
        if callable(factory) and 1 <= len(token) <= 128:
            resolved = await run_in_threadpool(resolve_session, token, clock(), factory())
        if resolved is not None:
            scope.setdefault("state", {})["principal"] = resolved.principal
            scope["state"]["identity_session"] = resolved.session
            if request.method not in SAFE_METHODS:
                csrf_cookie = security_cookie(request, csrf_cookie_name)
                csrf_header = request.headers.get("x-csrf-token", "")
                if (
                    len(csrf_cookie) > 128
                    or len(csrf_header) > 128
                    or not csrf_cookie
                    or not secrets_match(csrf_cookie, csrf_header)
                    or not csrf_matches(resolved, csrf_header)
                ):
                    response = JSONResponse(
                        {"detail": "Проверка CSRF не пройдена"},
                        status_code=403,
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def secrets_match(left: str, right: str) -> bool:
    import secrets

    return bool(left and right) and secrets.compare_digest(left, right)
