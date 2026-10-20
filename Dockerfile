# Use official Python slim image matching your required version in pyproject.toml
FROM python:3.13-slim

WORKDIR /app

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency files first for better layer caching
# (only re-installs when pyproject.toml or uv.lock changes)
COPY pyproject.toml uv.lock ./

# Install production dependencies only (no dev extras)
RUN uv sync --frozen --no-dev

# Copy the rest of the application source code
COPY . .

# Expose the port FastAPI listens on
EXPOSE 8000

# Run the app using uvicorn via uv (production-safe, no hot-reload)
CMD ["uv", "run", "fastapi", "run", "app/main.py", "--port", "8000"]
