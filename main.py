from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.config import settings
from app.db.mongodb import connect_to_mongo, close_mongo_connection
from app.bot.manager import bot_manager
from app.api.v1.tenants import router as tenants_router
from app.api.v1.webhooks import router as webhooks_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_to_mongo()
    await bot_manager.init_parent_bot()
    await bot_manager.init_active_tenants()
    yield
    await close_mongo_connection()

app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

app.include_router(tenants_router, prefix="/api/v1/tenants", tags=["Tenants"])
app.include_router(webhooks_router, prefix="/api/v1/webhooks", tags=["Webhooks"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)