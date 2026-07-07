"""
Structured request logging middleware — gives the RCA pipeline
end-to-end visibility at the Django tier, matching the structured
log_event() format already used by the Lambdas.
"""
import json
import time
import traceback
import uuid
from datetime import datetime, timezone


def _log(level, error_type, message, **kw):
    print(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "error_type": error_type,
        "message": message,
        "component": "django-eb",
        **kw,
    }))


class RequestLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = str(uuid.uuid4())[:8]
        start = time.time()

        response = self.get_response(request)

        duration_ms = round((time.time() - start) * 1000, 1)
        level = "INFO"
        error_type = "REQUEST"
        if response.status_code >= 500:
            level, error_type = "ERROR", "HTTP_5XX"
        elif response.status_code >= 400:
            level, error_type = "WARNING", "HTTP_4XX"

        _log(level, error_type,
             f"{request.method} {request.path} -> {response.status_code}",
             request_id=request_id,
             method=request.method,
             path=request.path,
             status=response.status_code,
             duration_ms=duration_ms,
             username=request.session.get("username", "anonymous"))
        return response

    def process_exception(self, request, exception):
        _log("ERROR", "UNHANDLED_EXCEPTION",
             f"{type(exception).__name__} in {request.method} {request.path}: {exception}",
             path=request.path,
             method=request.method,
             username=request.session.get("username", "anonymous"),
             traceback=traceback.format_exc()[-1500:])
        return None  # let Django's normal error handling continue