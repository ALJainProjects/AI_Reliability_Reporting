"""Statuspage.io API fetcher for incident data."""

import logging
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..models import AffectedComponent, Incident, IncidentUpdate, StatusPage
from .base import BaseFetcher

logger = logging.getLogger(__name__)


class StatusPageAPIFetcher(BaseFetcher):
    """Fetcher for Statuspage.io API v2 endpoints."""

    def __init__(self, rate_limit: float = 1.0, timeout: float = 30.0):
        """
        Initialize the API fetcher.

        Args:
            rate_limit: Maximum requests per second
            timeout: Request timeout in seconds
        """
        super().__init__(rate_limit)
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": "ReliabilityReporter/1.0",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _normalize_base_url(self, url: str) -> str:
        """
        Normalize base URL for API requests.

        Handles various URL formats:
        - https://status.company.com
        - https://status.company.com/
        - https://status.company.com/history
        """
        parsed = urlparse(url)
        # Extract just scheme and netloc (domain)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _get_api_url(self, base_url: str, endpoint: str) -> str:
        """Get full API URL for an endpoint."""
        normalized = self._normalize_base_url(base_url)
        return urljoin(normalized, f"/api/v2/{endpoint}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _fetch_json(self, url: str) -> dict:
        """
        Fetch JSON from URL with rate limiting and retries.

        Args:
            url: URL to fetch

        Returns:
            Parsed JSON response
        """
        await self.rate_limiter.acquire()
        client = await self._get_client()

        logger.debug(f"Fetching: {url}")
        response = await client.get(url)
        response.raise_for_status()

        return response.json()

    def _parse_datetime(self, dt_str: str | None) -> datetime | None:
        """Parse ISO 8601 datetime string."""
        if not dt_str:
            return None
        # Handle various ISO 8601 formats
        try:
            # Try with microseconds and Z
            if dt_str.endswith("Z"):
                dt_str = dt_str[:-1] + "+00:00"
            return datetime.fromisoformat(dt_str)
        except ValueError:
            logger.warning(f"Could not parse datetime: {dt_str}")
            return None

    def _parse_incident(self, data: dict, company_name: str, base_url: str) -> Incident:
        """
        Parse incident data from API response.

        Args:
            data: Raw incident data from API
            company_name: Company name for labeling
            base_url: Source URL for reference

        Returns:
            Parsed Incident object
        """
        # Parse incident updates
        updates = []
        for update_data in data.get("incident_updates", []):
            updates.append(
                IncidentUpdate(
                    id=update_data.get("id", ""),
                    status=update_data.get("status", ""),
                    body=update_data.get("body", ""),
                    created_at=self._parse_datetime(update_data.get("created_at"))
                    or datetime.now(),
                )
            )

        # Parse affected components
        components = []
        for comp_data in data.get("components", []):
            components.append(
                AffectedComponent(
                    id=comp_data.get("id", ""),
                    name=comp_data.get("name", ""),
                    status=comp_data.get("status"),
                )
            )

        return Incident(
            id=data.get("id", ""),
            name=data.get("name", "Unknown Incident"),
            status=data.get("status", "unknown"),
            impact=data.get("impact", "none"),
            created_at=self._parse_datetime(data.get("created_at")) or datetime.now(),
            updated_at=self._parse_datetime(data.get("updated_at")) or datetime.now(),
            resolved_at=self._parse_datetime(data.get("resolved_at")),
            started_at=self._parse_datetime(data.get("started_at")),
            incident_updates=updates,
            affected_components=components,
            source_url=base_url,
            company_name=company_name,
            shortlink=data.get("shortlink"),
        )

    async def fetch_incidents(
        self,
        base_url: str,
        company_name: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[Incident]:
        """
        Fetch incidents from Statuspage.io API.

        Note: The API typically returns only ~4-8 months of history.
        For older data, use the HTML scraper fallback.

        Args:
            base_url: Base URL of the status page
            company_name: Name of the company
            start_date: Start of timeframe (inclusive)
            end_date: End of timeframe (inclusive)

        Returns:
            List of Incident objects within the timeframe
        """
        url = self._get_api_url(base_url, "incidents.json")

        try:
            data = await self._fetch_json(url)
        except httpx.HTTPStatusError as e:
            logger.error(f"API request failed for {company_name}: {e}")
            return []
        except Exception as e:
            logger.error(f"Error fetching incidents for {company_name}: {e}")
            return []

        incidents = []
        for incident_data in data.get("incidents", []):
            incident = self._parse_incident(incident_data, company_name, base_url)
            incidents.append(incident)

        logger.info(f"Fetched {len(incidents)} incidents from {company_name} API")

        # Filter by timeframe
        filtered = self.filter_by_timeframe(incidents, start_date, end_date)
        logger.info(f"After filtering: {len(filtered)} incidents in timeframe")

        return filtered

    async def fetch_unresolved_incidents(
        self, base_url: str, company_name: str
    ) -> list[Incident]:
        """
        Fetch only unresolved/active incidents.

        Args:
            base_url: Base URL of the status page
            company_name: Name of the company

        Returns:
            List of unresolved Incident objects
        """
        url = self._get_api_url(base_url, "incidents/unresolved.json")

        try:
            data = await self._fetch_json(url)
        except Exception as e:
            logger.error(f"Error fetching unresolved incidents for {company_name}: {e}")
            return []

        incidents = []
        for incident_data in data.get("incidents", []):
            incident = self._parse_incident(incident_data, company_name, base_url)
            incidents.append(incident)

        return incidents

    async def fetch_status_page_info(self, base_url: str) -> StatusPage:
        """
        Fetch metadata about a status page.

        Args:
            base_url: Base URL of the status page

        Returns:
            StatusPage metadata object
        """
        normalized = self._normalize_base_url(base_url)

        # Fetch summary for page info
        summary_url = self._get_api_url(base_url, "summary.json")

        try:
            data = await self._fetch_json(summary_url)
        except Exception as e:
            logger.error(f"Error fetching status page info: {e}")
            # Return minimal info on error
            return StatusPage(
                company_name="Unknown",
                base_url=normalized,
                api_base_url=normalized,
                has_api=False,
            )

        # Parse components
        components = []
        for comp_data in data.get("components", []):
            components.append(
                AffectedComponent(
                    id=comp_data.get("id", ""),
                    name=comp_data.get("name", ""),
                    status=comp_data.get("status"),
                )
            )

        page_data = data.get("page", {})

        return StatusPage(
            company_name=page_data.get("name", "Unknown"),
            base_url=normalized,
            api_base_url=normalized,
            has_api=True,
            components=components,
        )

    async def check_api_available(self, base_url: str) -> bool:
        """
        Check if the Statuspage.io API is available for this URL.

        Args:
            base_url: Base URL to check

        Returns:
            True if API is available
        """
        url = self._get_api_url(base_url, "status.json")

        try:
            await self._fetch_json(url)
            return True
        except Exception:
            return False
