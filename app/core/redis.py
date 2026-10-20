from fastapi import Request
import redis.asyncio as redis


async def get_redis(request: Request) -> redis.Redis:
    return request.app.state.redis
