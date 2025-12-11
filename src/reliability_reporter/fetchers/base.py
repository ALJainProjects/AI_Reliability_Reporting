"""Base fetcher interface for status page data."""

import asyncio
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from ..models import Incident, StatusPage


def make_aware(dt: datetime) -> datetime:
    """Make a datetime timezone-aware (UTC) if it's naive."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def make_naive(dt: datetime) -> datetime:
    """Make a datetime naive by removing timezone info."""
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


class RateLimiter:
    """Token bucket rate limiter for API requests."""

    def __init__(self, rate: float = 1.0):
        """
        Initialize rate limiter.

        Args:
            rate: Maximum requests per second
        """
        self.rate = rate
        self.tokens = rate
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a request token is available."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_update = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1


class BaseFetcher(ABC):
    """Abstract base class for status page fetchers."""

    def __init__(self, rate_limit: float = 1.0):
        """
        Initialize the fetcher.

        Args:
            rate_limit: Maximum requests per second
        """
        self.rate_limiter = RateLimiter(rate_limit)

    @abstractmethod
    async def fetch_incidents(
        self,
        base_url: str,
        company_name: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[Incident]:
        """
        Fetch incidents from a status page.

        Args:
            base_url: Base URL of the status page (e.g., https://status.example.com)
            company_name: Name of the company for labeling
            start_date: Start of the timeframe (inclusive)
            end_date: End of the timeframe (inclusive)

        Returns:
            List of Incident objects
        """
        pass

    @abstractmethod
    async def fetch_status_page_info(self, base_url: str) -> StatusPage:
        """
        Fetch metadata about a status page.

        Args:
            base_url: Base URL of the status page

        Returns:
            StatusPage metadata object
        """
        pass

    def filter_by_timeframe(
        self,
        incidents: list[Incident],
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[Incident]:
        """
        Filter incidents by timeframe.

        Args:
            incidents: List of incidents to filter
            start_date: Start of timeframe (inclusive)
            end_date: End of timeframe (inclusive)

        Returns:
            Filtered list of incidents
        """
        # Normalize dates to naive for comparison (remove timezone info)
        start_naive = make_naive(start_date) if start_date else None
        end_naive = make_naive(end_date) if end_date else None

        filtered = []
        for incident in incidents:
            incident_date = incident.started_at or incident.created_at
            incident_date_naive = make_naive(incident_date)

            if start_naive and incident_date_naive < start_naive:
                continue
            if end_naive and incident_date_naive > end_naive:
                continue

            filtered.append(incident)

        return filtered
