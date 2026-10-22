import time
import logging
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.database import engine
from app.core.response import api_response

logger = logging.getLogger("app.health")

router = APIRouter()

# Track when the app started so we can report uptime
_start_time = time.time()


@router.get(
    "/health",
    summary="Health Check",
    description="Returns the live status of the application, database, and Redis.",
    tags=["health"],
)
async def health_check(request: Request):
    """
    Probes the database and Redis connections and returns a structured
    health report.  Returns HTTP 200 when everything is healthy,
    HTTP 503 when one or more dependencies are unreachable.
    """
    checks: dict[str, str] = {}
    healthy = True

    # --- Database probe ---
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        logger.error("Health check – database unreachable: %s", exc)
        checks["database"] = "unreachable"
        healthy = False

    # --- Redis probe ---
    try:
        redis_client = request.app.state.redis
        await redis_client.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.error("Health check – Redis unreachable: %s", exc)
        checks["redis"] = "unreachable"
        healthy = False

    # --- Uptime ---
    uptime_seconds = int(time.time() - _start_time)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"

    payload = {
        "status": "ok" if healthy else "degraded",
        "uptime": uptime_str,
        "checks": checks,
    }

    http_status = status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=http_status,
        content=api_response(
            status="success" if healthy else "error",
            message="All systems operational." if healthy else "One or more dependencies are unreachable.",
            data=payload,
        ),
    )
