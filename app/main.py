from app.core.limiter import limiter
from app.core.config import settings
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from app.core.database import engine
from app.core.response import api_response
from app.auth.routes import router as auth_router
from app.customers.routes import router as customers_router
from app.vendors.routes import router as vendors_router
from app.riders.routes import router as riders_router
from app.health.routes import router as health_router
# Ensures SQLAlchemy models are registered with Base.metadata
import app.models as _models_init
import logging
from sqlalchemy import text
import redis.asyncio as redis


from slowapi.errors import RateLimitExceeded

# logger configuration
is_production = settings.environment.lower() == "production"

handlers = [
    logging.StreamHandler()
]

if not is_production:
    handlers.append(logging.FileHandler("app.log", mode="a"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=handlers
)

# Suppress noisy third-party loggers (e.g. watchfiles reload notifications)
logging.getLogger("watchfiles").setLevel(logging.WARNING)

logger = logging.getLogger("app.main")


# lifespan configuration
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Test DB connection on startup
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection established successfully.")
    except Exception:
        logger.critical(
            "FATAL: Could not connect to the database on application startup!", exc_info=True)

    # Create Redis client (connection pool) and verify it's reachable
    _app.state.redis = redis.from_url(
        settings.redis_url, decode_responses=True)
    try:
        await _app.state.redis.ping()
        logger.info("Redis connection established successfully.")
    except Exception:
        logger.error(
            "Could not connect to Redis on application startup!", exc_info=True)

    yield

    logger.info("Application shutting down cleanly...")
    await _app.state.redis.aclose()
    await engine.dispose()


app = FastAPI(lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
ALLOWED_ORIGINS = [
    settings.frontend_url,
    "http://localhost:3000",
    "http://localhost:5173",
    "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

# Attach limiter to FastAPI app state
app.state.limiter = limiter

# exception handlers


@app.exception_handler(RateLimitExceeded)
async def custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    client_ip = request.client.host if request.client else "unknown"
    logger.warning("Rate limit exceeded for IP %s on %s %s",
                   client_ip, request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content=api_response(
            status="error", message=f"Rate limit exceeded: {exc.detail}", data=None)
    )


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code >= 500:
        logger.error("HTTP %s Error on %s %s: %s", exc.status_code,
                     request.method, request.url.path, exc.detail)
    else:
        logger.warning("HTTP %s Warning on %s %s: %s", exc.status_code,
                       request.method, request.url.path, exc.detail)

    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content=api_response(
            status="error", message=str(exc.detail), data=None)
    )


@app.exception_handler(RequestValidationError)
async def custom_validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning("Validation error on %s %s: %s",
                   request.method, request.url.path, exc.errors())

    # Pydantic v2 puts the original exception object inside `ctx` (e.g. ValueError).
    # json.dumps() cannot serialize Python exceptions, so we stringify them here.
    def make_serializable(errors: list) -> list:
        result = []
        for error in errors:
            err = dict(error)
            if "ctx" in err:
                err["ctx"] = {
                    k: str(v) if isinstance(v, Exception) else v
                    for k, v in err["ctx"].items()
                }
            result.append(err)
        return result

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=api_response(
            status="error", message="Validation error", data=make_serializable(exc.errors()))
    )


@app.exception_handler(Exception)
async def global_unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled Exception on %s %s", request.method,
                 request.url.path, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=api_response(
            status="error", message="An unexpected internal server error occurred.", data=None)
    )

# security headers middleware


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)

    # Prevent your API responses from being embedded in iframes on other sites
    response.headers["X-Frame-Options"] = "SAMEORIGIN"

    # Prevent browsers from MIME-sniffing the content type (e.g. treating a
    # JSON response as HTML and executing injected scripts)
    response.headers["X-Content-Type-Options"] = "nosniff"

    # Tell browsers to activate their built-in XSS filter
    response.headers["X-XSS-Protection"] = "1; mode=block"

    # Control how much referrer info is sent when navigating away from your API
    if "Referrer-Policy" not in response.headers:
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    return response

# Infrastructure / observability
app.include_router(health_router, prefix='/api/v1', tags=['health'])

# Auth & user management
app.include_router(auth_router, prefix='/api/v1/auth', tags=['auth'])

# Domain-specific routes (role-protected)
app.include_router(
    customers_router, prefix='/api/v1/customers', tags=['customers'])
app.include_router(vendors_router, prefix='/api/v1/vendors', tags=['vendors'])
app.include_router(riders_router, prefix='/api/v1/riders', tags=['riders'])
