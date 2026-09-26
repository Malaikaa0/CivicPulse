"""Rate-limit dependency for POST /api/complaints. HTTP concerns only: who is the client, and how
a refusal looks. The counting is done by RateLimiter."""

import ipaddress
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.cache_wiring import get_rate_limiter
from app.config import Settings, get_settings
from app.services.rate_limit import RateLimiter


def client_ip(request: Request, *, trust_forwarded_for: bool) -> str:
    """The address to rate-limit.

    By default this is the TCP peer. X-Forwarded-For is only read when trust_forwarded_for is set,
    because any client can send that header: trusting it without a proxy that overwrites it lets
    a caller pick a fresh identity per request and never hit the limit. When it is trusted, the
    first entry is the original client (later ones are proxies that appended themselves), so the
    ingress must be configured to set the header rather than pass the caller's value through.
    """
    peer = request.client.host if request.client else "unknown"
    if not trust_forwarded_for:
        return peer
    first_hop = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    try:
        ipaddress.ip_address(first_hop)
    except ValueError:
        # Missing or garbage: fall back to the peer instead of using arbitrary text as a key.
        return peer
    return first_hop


def enforce_rate_limit(
    request: Request,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    decision = limiter.check(client_ip(request, trust_forwarded_for=settings.trust_forwarded_for))
    if decision.allowed:
        return
    raise HTTPException(
        status_code=429,
        detail=(
            f"Rate limit exceeded: at most {limiter.limit} requests per "
            f"{limiter.window_seconds} seconds. Retry in {decision.retry_after_seconds} seconds."
        ),
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )
