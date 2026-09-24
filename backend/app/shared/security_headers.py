"""Security response headers for the cookie-authenticated browser application."""

from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _frame_source(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "'none'"
    return f"{parsed.scheme}://{parsed.netloc}"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, legacy_origin: str):
        self.app = app
        legacy_frame = _frame_source(legacy_origin)
        self.content_security_policy = "; ".join(
            (
                "default-src 'self'",
                "base-uri 'self'",
                "object-src 'none'",
                "form-action 'self'",
                "frame-ancestors 'none'",
                f"frame-src {legacy_frame}",
                "script-src 'self'",
                "style-src 'self'",
                "img-src 'self' data:",
                "font-src 'self'",
                f"connect-src 'self' {legacy_frame}",
            )
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
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
                if path.startswith("/api") or content_type.startswith("text/html"):
                    headers["Cache-Control"] = "no-store"
                if scope.get("scheme") == "https":
                    headers["Strict-Transport-Security"] = (
                        "max-age=31536000; includeSubDomains"
                    )
            await send(message)

        await self.app(scope, receive, send_with_headers)
