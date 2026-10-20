import asyncio
import sys
from sqlalchemy import text
from app.core.database import engine

async def reset():
    async with engine.begin() as conn:
        print("Dropping and recreating public schema...")
        await conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    print("Schema reset successfully!")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(reset())
