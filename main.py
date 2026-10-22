import asyncio
import os
from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.config import settings
from app.db.mongodb import connect_to_mongo, close_mongo_connection
from app.bot.manager import bot_manager
from app.api.v1.tenants import router as tenants_router
from app.api.v1.webhooks import router as webhooks_router
from app.api.v1.cron import router as cron_router
from app.api.v1.pages import router as pages_router


def _is_serverless() -> bool:
    return bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"))


async def _flush_state_loop() -> None:
    """Safety net: persist buffered FSM state even outside webhook requests."""
    while True:
        await asyncio.sleep(3)
        await bot_manager.flush_state()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_to_mongo()
    flush_task = asyncio.create_task(_flush_state_loop())
    if settings.INIT_WEBHOOKS_ON_STARTUP and not _is_serverless():
        await bot_manager.init_parent_bot()
        await bot_manager.init_active_tenants()
    yield
    flush_task.cancel()
    await bot_manager.flush_state()
    await close_mongo_connection()

app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "service": settings.PROJECT_NAME,
        "message": "DAMN Booking Bot is running"
    }

app.include_router(tenants_router, prefix="/api/v1/tenants", tags=["Tenants"])
app.include_router(webhooks_router, prefix="/api/v1/webhooks", tags=["Webhooks"])
app.include_router(cron_router, prefix="/api/v1/cron", tags=["Cron"])
app.include_router(pages_router, tags=["Pages"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)