from fastapi import APIRouter, HTTPException, status
from app.models.tenant import TenantModel, ServiceModel
from app.db.mongodb import db
from app.bot.manager import bot_manager

router = APIRouter()

@router.post("/", response_model=TenantModel, status_code=status.HTTP_201_CREATED)
async def create_tenant(tenant: TenantModel):
    existing = await db.db.tenants.find_one({"tenant_id": tenant.tenant_id})
    if existing:
        raise HTTPException(status_code=400, detail="Tenant already exists")

    await db.db.tenants.insert_one(tenant.model_dump())
    
    try:
        await bot_manager.register_tenant_webhook(tenant.bot_config.token)
    except Exception:
        pass

    return tenant

@router.post("/services", response_model=ServiceModel)
async def create_service(service: ServiceModel):
    await db.db.services.insert_one(service.model_dump())
    from app.services import service_cache

    service_cache.invalidate(service.tenant_id)
    return service