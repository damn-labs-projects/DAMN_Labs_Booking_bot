BLOCKED_SUBSCRIPTION_STATUSES = frozenset({"expired", "cancelled", "past_due"})


def is_blocked(tenant: dict | None) -> bool:
    if not isinstance(tenant, dict):
        return False
    subscription = tenant.get("subscription") or {}
    if not isinstance(subscription, dict):
        return False
    return subscription.get("status") in BLOCKED_SUBSCRIPTION_STATUSES


def status_of(tenant: dict | None) -> str:
    if not isinstance(tenant, dict):
        return "unknown"
    subscription = tenant.get("subscription") or {}
    if not isinstance(subscription, dict):
        return "unknown"
    return subscription.get("status") or "trial"


def period_end_of(tenant: dict | None):
    if not isinstance(tenant, dict):
        return None
    subscription = tenant.get("subscription") or {}
    if not isinstance(subscription, dict):
        return None
    return subscription.get("current_period_end")
