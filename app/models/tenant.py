from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
import uuid

class OwnerInfo(BaseModel):
    name: str
    phone: str
    telegram_id: int

class BotConfig(BaseModel):
    token: str
    username: str
    is_active: bool = True

class WorkingHours(BaseModel):
    start: str = "09:00"
    end: str = "20:00"
    slot_duration_minutes: int = 60

class Subscription(BaseModel):
    status: str = "trial"  # 'trial', 'active', 'expired', 'cancelled'
    paddle_subscription_id: Optional[str] = None
    plan_id: Optional[str] = None
    current_period_end: Optional[datetime] = None

class TenantModel(BaseModel):
    tenant_id: str
    name: str
    category: str
    owner_info: OwnerInfo
    bot_config: BotConfig
    working_hours: WorkingHours
    notification_chat_id: str
    subscription: Subscription = Field(default_factory=Subscription)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class ServiceModel(BaseModel):
    tenant_id: str
    service_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    description: Optional[str] = ""
    price: float
    currency: str = "KGS"
    duration_minutes: int = 60
    is_active: bool = True

class ClientInfo(BaseModel):
    telegram_id: int
    full_name: str
    phone: str

class AppointmentModel(BaseModel):
    tenant_id: str
    appointment_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_info: ClientInfo
    service_id: str
    staff_id: Optional[str] = None
    start_time: datetime
    end_time: datetime
    status: str = "confirmed"
    created_at: datetime = Field(default_factory=datetime.utcnow)