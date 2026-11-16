"""FastAPI modular HTTP interface."""

from vertex.interfaces.http import (
    account,
    admin,
    analytics,
    auth,
    catalog,
    connections,
    health,
    homeassistant,
    jobs,
    library,
    portability,
    push,
    telemetry,
    wishlist,
)

__all__ = [
    "account",
    "admin",
    "analytics",
    "auth",
    "catalog",
    "connections",
    "health",
    "homeassistant",
    "jobs",
    "library",
    "portability",
    "push",
    "telemetry",
    "wishlist",
]
