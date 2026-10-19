from datetime import datetime, timedelta

from pymongo.errors import DuplicateKeyError

from app.db.mongodb import db
from app.models.tenant import AppointmentModel, ClientInfo
from app.services import subscription


class BookingRejected(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

class BookingService:
    @staticmethod
    async def get_available_slots(tenant_id: str, service_id: str, date_str: str) -> list[str]:
        if db.db is None:
            return []
        tenant = await db.db.tenants.find_one({"tenant_id": tenant_id})
        service = await db.db.services.find_one({"tenant_id": tenant_id, "service_id": service_id})
        if not tenant or not service:
            return []

        work_start = tenant["working_hours"]["start"]
        work_end = tenant["working_hours"]["end"]
        duration = service.get("duration_minutes", 60)

        start_dt = datetime.strptime(f"{date_str} {work_start}", "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(f"{date_str} {work_end}", "%Y-%m-%d %H:%M")

        day_start = datetime.strptime(date_str, "%Y-%m-%d")
        day_end = day_start + timedelta(days=1)

        existing_appointments = await db.db.appointments.find({
            "tenant_id": tenant_id,
            "status": {"$ne": "cancelled"},
            "start_time": {"$gte": day_start, "$lt": day_end}
        }).to_list(length=200)

        slots = []
        curr = start_dt
        while curr + timedelta(minutes=duration) <= end_dt:
            slot_end = curr + timedelta(minutes=duration)
            
            is_busy = False
            for app in existing_appointments:
                app_start = app["start_time"]
                app_end = app["end_time"]
                if max(curr, app_start) < min(slot_end, app_end):
                    is_busy = True
                    break
            
            if not is_busy:
                slots.append(curr.strftime("%H:%M"))

            curr += timedelta(minutes=duration)

        return slots

    @staticmethod
    async def create_appointment(tenant_id: str, service_id: str, telegram_id: int, full_name: str, phone: str, date_str: str, time_str: str):
        if db.db is None:
            raise BookingRejected("Сервис временно недоступен. Попробуйте чуть позже.")

        tenant = await db.db.tenants.find_one({"tenant_id": tenant_id})
        service = await db.db.services.find_one({"tenant_id": tenant_id, "service_id": service_id})

        if not tenant or not service:
            raise BookingRejected("Заведение или услуга больше недоступны. Начните заново — /start.")

        if subscription.is_blocked(tenant):
            raise BookingRejected("Обслуживание заведения временно приостановлено.")

        start_time = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        end_time = start_time + timedelta(minutes=service["duration_minutes"])

        await BookingService.assert_slot_free(tenant_id, start_time, end_time)

        appointment = AppointmentModel(
            tenant_id=tenant_id,
            client_info=ClientInfo(telegram_id=telegram_id, full_name=full_name, phone=phone),
            service_id=service_id,
            start_time=start_time,
            end_time=end_time,
            status="confirmed"
        )

        try:
            await db.db.appointments.insert_one(appointment.model_dump())
        except DuplicateKeyError:
            raise BookingRejected(
                "Это время уже заняли. Выберите другой слот — /start."
            )
        return appointment.model_dump(), tenant, service

    @staticmethod
    async def assert_slot_free(tenant_id: str, start_time: datetime, end_time: datetime) -> None:
        if db.db is None:
            raise BookingRejected("Сервис временно недоступен. Попробуйте чуть позже.")

        conflict = await db.db.appointments.find_one({
            "tenant_id": tenant_id,
            "status": {"$ne": "cancelled"},
            "start_time": {"$lt": end_time},
            "end_time": {"$gt": start_time},
        })
        if conflict:
            raise BookingRejected(
                "Это время уже заняли. Выберите другой слот — /start."
            )