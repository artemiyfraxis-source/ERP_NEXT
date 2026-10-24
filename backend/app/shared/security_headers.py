"""Security response headers for the cookie-authenticated browser application."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.content_security_policy = "; ".join(
            (
                "default-src 'self'",
                "base-uri 'self'",
                "object-src 'none'",
                "form-action 'self'",
                "frame-ancestors 'none'",
                "frame-src 'none'",
                "script-src 'self'",
                "style-src 'self'",
                "img-src 'self' data:",
                "font-src 'self'",
                "connect-src 'self'",
            )
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                # A stored file carries its own, stricter policy (shared/stored_files.py) — keep it.
                if "content-security-policy" not in headers:
                    headers["Content-Security-Policy"] = self.content_security_policy
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = (
                    "camera=(), microphone=(), geolocation=(), payment=()"
                )
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                content_type = headers.get("content-type", "")
                path = str(scope.get("path", ""))
                # A route may let the viewer's own browser keep an immutable picture (`private, …`: never a shared
                # cache) — the standard figures and connector pictures; every other API answer is no-store.
                own = headers.get("cache-control", "")
                if (path.startswith("/api") and not own.startswith("private")) or content_type.startswith("text/html"):
                    headers["Cache-Control"] = "no-store"
                if scope.get("scheme") == "https":
                    headers["Strict-Transport-Security"] = (
                        "max-age=31536000; includeSubDomains"
                    )
            await send(message)

        await self.app(scope, receive, send_with_headers)
