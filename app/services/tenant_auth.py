import hashlib
import hmac


def hash_owner_password(tenant_id: str, password: str) -> str:
    return hashlib.sha256(f"{tenant_id}:{password}".encode("utf-8")).hexdigest()


def verify_owner_password(tenant: dict, password: str) -> bool:
    stored = (tenant.get("owner") or {}).get("password_hash")
    if not stored:
        return False
    expected = hash_owner_password(tenant.get("tenant_id", ""), password or "")
    return hmac.compare_digest(stored, expected)


def owner_is_authorized(tenant: dict, telegram_id) -> bool:
    ids = (tenant.get("owner") or {}).get("telegram_ids") or []
    return telegram_id in ids


def owner_password_set(tenant: dict) -> bool:
    return bool((tenant.get("owner") or {}).get("password_hash"))
