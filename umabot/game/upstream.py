"""Coordinate request budgets and cooldowns for a shared HTTP session."""

from collections import deque
from contextlib import asynccontextmanager
from datetime import timezone
from email.utils import parsedate_to_datetime
from time import monotonic, time
from urllib.parse import urlsplit

import aiohttp


UMAPYOI_LIMITS = ((1, 10), (60, 500), (3600, 7200), (86400, 172800))


class UpstreamCooldown(aiohttp.ClientError):
    def __init__(self, host, retry_at):
        super().__init__(f"{host} is rate limited")
        self.retry_at = retry_at


def retry_after_seconds(value):
    if not isinstance(value, str) or not (value := value.strip()):
        return None
    if value.isascii() and value.isdecimal():
        try:
            return float(value) if len(value) < 309 else None
        except ValueError:
            return None
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, date.timestamp() - time())
    except (ValueError, TypeError, OverflowError, OSError):
        return None


class HostBudget:
    def __init__(self, limits):
        self.windows = [(seconds, limit, deque()) for seconds, limit in limits]
        self.until = 0
        self.backoff = 60
        self.generation = 0

    def admit(self, host, now):
        if now < self.until:
            raise UpstreamCooldown(host, self.until)
        retry_at = now
        for seconds, limit, requests in self.windows:
            while requests and requests[0] + seconds <= now:
                requests.popleft()
            if len(requests) >= limit:
                retry_at = max(retry_at, requests[0] + seconds)
        if retry_at > now:
            raise UpstreamCooldown(host, retry_at)
        for _, _, requests in self.windows:
            requests.append(now)

    def rate_limited(self, delay, now):
        # Concurrent requests already in flight belong to the same throttling episode.
        if delay is None:
            if now < self.until:
                return
            delay = self.backoff
            self.backoff = min(self.backoff * 2, 3600)
        self.until = max(self.until, now + delay)
        self.generation += 1


class UpstreamHTTP:
    def __init__(self, session):
        self.session = session
        self.hosts = {}

    def cooling_down(self, host):
        budget = self.hosts.get(host)
        return budget is not None and monotonic() < budget.until

    @asynccontextmanager
    async def get(self, url, **kwargs):
        host = urlsplit(url).hostname
        budget = self.hosts.get(host)
        if budget is None:
            budget = self.hosts[host] = HostBudget(UMAPYOI_LIMITS if host == "umapyoi.net" else ())
        budget.admit(host, monotonic())
        generation = budget.generation
        async with self.session.get(url, **kwargs) as response:
            if response.status == 429:
                delay = retry_after_seconds(response.headers.get("Retry-After"))
                budget.rate_limited(delay, monotonic())
                raise UpstreamCooldown(host, budget.until)
            if response.status < 400 and generation == budget.generation:
                budget.backoff = 60
            yield response
