"""ASGI request-body limit independent of proxy configuration."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    def __init__(
        self, app: ASGIApp, max_bytes: int = 1_048_576, path_limits: dict[str, int] | None = None,
    ):
        self.app = app
        self.max_bytes = max_bytes
        # File uploads that travel as base64 JSON (КД, файл расчёта) get their own ceiling by path prefix.
        self.path_limits = dict(path_limits or {})

    def _limit(self, scope: Scope) -> int:
        path = str(scope.get("path") or "")
        return next(
            (limit for prefix, limit in self.path_limits.items() if path.startswith(prefix)),
            self.max_bytes,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_bytes = self._limit(scope)
        content_length = dict(scope.get("headers", [])).get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                await self._reject(scope, receive, send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {"detail": "Тело запроса слишком большое"},
            status_code=413,
        )
        await response(scope, receive, send)


class _BodyTooLarge(Exception):
    pass
