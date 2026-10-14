"""ASGI request correlation, bounded bodies, and consistent failure envelopes."""

import logging
import re
from time import monotonic
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from material_allocation_copilot.errors import ApplicationError, ErrorCode

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 5 * 1024 * 1024
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def error_response(error: ApplicationError, request_id: str, status: int) -> JSONResponse:
    headers = {"X-Request-ID": request_id}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        {"error": error.to_dict(), "request_id": request_id}, status_code=status, headers=headers
    )


class RequestMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied_id = Headers(scope=scope).get("x-request-id", "")
        request_id = supplied_id if _REQUEST_ID.fullmatch(supplied_id) else uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = False
        started_at = monotonic()
        status_code = 500

        async def correlated_send(message):
            nonlocal started, status_code
            if message["type"] == "http.response.start":
                started = True
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                for name, value in _SECURITY_HEADERS.items():
                    headers[name] = value
            await send(message)

        try:
            content_length = Headers(scope=scope).get("content-length")
            if content_length is not None:
                try:
                    length = int(content_length)
                    if length < 0:
                        raise ValueError
                except ValueError:
                    await error_response(
                        ApplicationError(ErrorCode.INPUT_INVALID, "Invalid Content-Length."),
                        request_id,
                        400,
                    )(scope, receive, correlated_send)
                    return
                if length > MAX_BODY_BYTES:
                    await error_response(
                        ApplicationError(ErrorCode.PAYLOAD_TOO_LARGE, "Request body is too large."),
                        request_id,
                        413,
                    )(scope, receive, correlated_send)
                    return
            chunks = []
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    await error_response(
                        ApplicationError(ErrorCode.PAYLOAD_TOO_LARGE, "Request body is too large."),
                        request_id,
                        413,
                    )(scope, receive, correlated_send)
                    return
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            consumed = False

            async def buffered_receive():
                nonlocal consumed
                if consumed:
                    return await receive()
                consumed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

            await self.app(scope, buffered_receive, correlated_send)
        except Exception:
            if started:
                raise
            # Never log request bodies, bearer tokens, exception strings or SQL parameters.
            logger.error("Unhandled API error request_id=%s", request_id)
            await error_response(
                ApplicationError(ErrorCode.INTERNAL_ERROR, "An internal error occurred."),
                request_id,
                500,
            )(scope, receive, correlated_send)
        finally:
            duration_ms = max(0, int((monotonic() - started_at) * 1000))
            app = scope.get("app")
            metrics = getattr(getattr(app, "state", None), "metrics", None)
            if metrics is not None:
                metrics.observe(status_code=status_code, duration_ms=duration_ms)
            logger.info(
                "request_completed",
                extra={
                    "event": "request_completed",
                    "request_id": request_id,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )
