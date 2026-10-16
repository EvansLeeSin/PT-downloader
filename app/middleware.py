from starlette.responses import JSONResponse


class RequestBodyLimit:
    """Bound actual ASGI body bytes, including requests without Content-Length."""

    def __init__(self, app, max_bytes=65536):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        declared = headers.get(b"content-length", b"0")
        if not declared.isdigit() or len(declared) > 12 or int(declared) > self.max_bytes:
            return await self.reject(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                return await self.reject(scope, receive, send)
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)

    async def reject(self, scope, receive, send):
        response = JSONResponse(
            {"error": {"code": "BODY_TOO_LARGE", "message": "请求体超过大小限制"}},
            status_code=413,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
        await response(scope, receive, send)
