"""HTML scraper for Statuspage.io history pages."""

import logging
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from ..models import Incident, IncidentUpdate, StatusPage
from .base import BaseFetcher

logger = logging.getLogger(__name__)


class StatusPageHTMLScraper(BaseFetcher):
    """Scraper for Statuspage.io HTML history pages.

    Used as fallback when API doesn't have sufficient historical data.
    """

    def __init__(self, rate_limit: float = 1.0, timeout: float = 30.0):
        """
        Initialize the HTML scraper.

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
                    "User-Agent": "Mozilla/5.0 (compatible; ReliabilityReporter/1.0)",
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _normalize_base_url(self, url: str) -> str:
        """Normalize base URL."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _fetch_html(self, url: str) -> str:
        """
        Fetch HTML from URL with rate limiting and retries.

        Args:
            url: URL to fetch

        Returns:
            HTML content as string
        """
        await self.rate_limiter.acquire()
        client = await self._get_client()

        logger.debug(f"Fetching HTML: {url}")
        response = await client.get(url)
        response.raise_for_status()

        return response.text

    def _parse_datetime_from_text(self, text: str) -> datetime | None:
        """
        Parse datetime from various text formats found in HTML.

        Examples:
        - "Dec 5, 2024"
        - "Dec 5, 2024 10:30 UTC"
        - "December 5, 2024"
        """
        if not text:
            return None

        text = text.strip()

        # Common date patterns
        patterns = [
            (r"(\w+)\s+(\d{1,2}),?\s+(\d{4})\s+(\d{1,2}):(\d{2})", "%b %d %Y %H:%M"),
            (r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", "%b %d %Y"),
            (r"(\d{4})-(\d{2})-(\d{2})", "%Y-%m-%d"),
        ]

        for pattern, date_format in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    # Reconstruct date string from match
                    groups = match.groups()
                    if len(groups) == 5:  # With time
                        date_str = f"{groups[0]} {groups[1]} {groups[2]} {groups[3]}:{groups[4]}"
                    elif len(groups) == 3:  # Date only
                        if groups[0].isdigit():  # YYYY-MM-DD format
                            date_str = f"{groups[0]}-{groups[1]}-{groups[2]}"
                        else:  # Month Day Year
                            date_str = f"{groups[0]} {groups[1]} {groups[2]}"
                    else:
                        continue

                    return datetime.strptime(date_str, date_format)
                except ValueError:
                    continue

        logger.warning(f"Could not parse date: {text}")
        return None

    def _extract_impact_from_class(self, element) -> str:
        """Extract impact level from CSS classes."""
        classes = element.get("class", [])
        if isinstance(classes, str):
            classes = classes.split()

        for cls in classes:
            cls_lower = cls.lower()
            if "critical" in cls_lower or "red" in cls_lower:
                return "critical"
            elif "major" in cls_lower or "orange" in cls_lower:
                return "major"
            elif "minor" in cls_lower or "yellow" in cls_lower:
                return "minor"
            elif "maintenance" in cls_lower or "blue" in cls_lower:
                return "maintenance"

        return "none"

    def _parse_incident_from_html(
        self, incident_elem, company_name: str, base_url: str
    ) -> Incident | None:
        """
        Parse a single incident from HTML element.

        Args:
            incident_elem: BeautifulSoup element containing incident data
            company_name: Company name for labeling
            base_url: Source URL for reference

        Returns:
            Parsed Incident or None if parsing fails
        """
        try:
            # Extract title
            title_elem = incident_elem.select_one(
                ".incident-title, .incident-name, h3, .actual-title"
            )
            if not title_elem:
                return None
            title = title_elem.get_text(strip=True)

            # Extract ID from link or generate one
            link_elem = incident_elem.select_one("a[href*='/incidents/']")
            incident_id = ""
            shortlink = None
            if link_elem:
                href = link_elem.get("href", "")
                # Extract ID from URL like /incidents/abc123
                match = re.search(r"/incidents/([a-zA-Z0-9]+)", href)
                if match:
                    incident_id = match.group(1)
                shortlink = urljoin(base_url, href)

            if not incident_id:
                # Generate ID from title hash
                incident_id = f"html_{hash(title) & 0xFFFFFFFF:08x}"

            # Extract impact from CSS classes
            impact = self._extract_impact_from_class(incident_elem)

            # Extract dates
            date_elem = incident_elem.select_one(
                ".incident-date, .date, time, .timestamp, small"
            )
            date_text = date_elem.get_text(strip=True) if date_elem else ""
            created_at = self._parse_datetime_from_text(date_text) or datetime.now()

            # Try to extract resolved date
            resolved_elem = incident_elem.select_one(".resolved-date, .end-date")
            resolved_at = None
            if resolved_elem:
                resolved_at = self._parse_datetime_from_text(
                    resolved_elem.get_text(strip=True)
                )

            # Extract status from updates
            status = "resolved"  # Default for historical
            status_elem = incident_elem.select_one(
                ".incident-status, .status, .unresolved"
            )
            if status_elem:
                status_text = status_elem.get_text(strip=True).lower()
                if "investigating" in status_text:
                    status = "investigating"
                elif "identified" in status_text:
                    status = "identified"
                elif "monitoring" in status_text:
                    status = "monitoring"
                elif "resolved" in status_text:
                    status = "resolved"
                elif "postmortem" in status_text:
                    status = "postmortem"

            # Extract incident updates
            updates = []
            update_elems = incident_elem.select(
                ".incident-update, .update, .message-wrapper"
            )
            for i, update_elem in enumerate(update_elems):
                update_body = update_elem.select_one(".update-body, .message, p")
                if update_body:
                    update_status_elem = update_elem.select_one(
                        ".update-status, .status"
                    )
                    update_status = (
                        update_status_elem.get_text(strip=True).lower()
                        if update_status_elem
                        else status
                    )

                    update_date_elem = update_elem.select_one(
                        ".update-date, .timestamp, small"
                    )
                    update_date = (
                        self._parse_datetime_from_text(
                            update_date_elem.get_text(strip=True)
                        )
                        if update_date_elem
                        else created_at
                    )

                    updates.append(
                        IncidentUpdate(
                            id=f"{incident_id}_update_{i}",
                            status=update_status,
                            body=update_body.get_text(strip=True),
                            created_at=update_date or created_at,
                        )
                    )

            return Incident(
                id=incident_id,
                name=title,
                status=status,
                impact=impact,
                created_at=created_at,
                updated_at=created_at,
                resolved_at=resolved_at,
                started_at=created_at,
                incident_updates=updates,
                affected_components=[],
                source_url=base_url,
                company_name=company_name,
                shortlink=shortlink,
            )

        except Exception as e:
            logger.warning(f"Error parsing incident HTML: {e}")
            return None

    async def fetch_history_page(
        self, base_url: str, company_name: str, page: int = 1
    ) -> list[Incident]:
        """
        Fetch a single history page.

        Args:
            base_url: Base URL of the status page
            company_name: Company name for labeling
            page: Page number (1-indexed)

        Returns:
            List of incidents from this page
        """
        normalized = self._normalize_base_url(base_url)
        url = f"{normalized}/history?page={page}"

        try:
            html = await self._fetch_html(url)
        except Exception as e:
            logger.error(f"Error fetching history page {page} for {company_name}: {e}")
            return []

        soup = BeautifulSoup(html, "lxml")

        # Find incident containers
        incident_elems = soup.select(
            ".incident-container, .incident, .status-day, [data-incident-id]"
        )

        # If no incidents found with common selectors, try month sections
        if not incident_elems:
            incident_elems = soup.select(".month .incident, .incidents-list .incident")

        incidents = []
        for elem in incident_elems:
            incident = self._parse_incident_from_html(elem, company_name, normalized)
            if incident:
                incidents.append(incident)

        logger.debug(f"Parsed {len(incidents)} incidents from page {page}")
        return incidents

    async def fetch_incidents(
        self,
        base_url: str,
        company_name: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        max_pages: int = 50,
    ) -> list[Incident]:
        """
        Fetch incidents from HTML history pages.

        Args:
            base_url: Base URL of the status page
            company_name: Name of the company
            start_date: Start of timeframe (inclusive)
            end_date: End of timeframe (inclusive)
            max_pages: Maximum number of pages to fetch

        Returns:
            List of Incident objects within the timeframe
        """
        all_incidents = []
        page = 1

        while page <= max_pages:
            incidents = await self.fetch_history_page(base_url, company_name, page)

            if not incidents:
                logger.info(f"No more incidents found at page {page}")
                break

            all_incidents.extend(incidents)

            # Check if we've gone past the start date
            if start_date:
                oldest = min(
                    (i.started_at or i.created_at for i in incidents),
                    default=datetime.now(),
                )
                if oldest < start_date:
                    logger.info(
                        f"Reached incidents before start_date at page {page}"
                    )
                    break

            page += 1

        logger.info(
            f"Fetched {len(all_incidents)} total incidents from {company_name} HTML"
        )

        # Filter by timeframe and deduplicate
        filtered = self.filter_by_timeframe(all_incidents, start_date, end_date)

        # Deduplicate by ID
        seen_ids = set()
        unique_incidents = []
        for incident in filtered:
            if incident.id not in seen_ids:
                seen_ids.add(incident.id)
                unique_incidents.append(incident)

        logger.info(f"After filtering and dedup: {len(unique_incidents)} incidents")
        return unique_incidents

    async def fetch_status_page_info(self, base_url: str) -> StatusPage:
        """
        Fetch basic status page info from HTML.

        Args:
            base_url: Base URL of the status page

        Returns:
            StatusPage metadata object
        """
        normalized = self._normalize_base_url(base_url)

        try:
            html = await self._fetch_html(normalized)
        except Exception as e:
            logger.error(f"Error fetching status page info: {e}")
            return StatusPage(
                company_name="Unknown",
                base_url=normalized,
                has_api=False,
            )

        soup = BeautifulSoup(html, "lxml")

        # Try to find company name
        company_name = "Unknown"
        title_elem = soup.select_one("title, .page-title, h1")
        if title_elem:
            title_text = title_elem.get_text(strip=True)
            # Clean up common suffixes
            for suffix in [" Status", " System Status", " - Status"]:
                if title_text.endswith(suffix):
                    title_text = title_text[: -len(suffix)]
            company_name = title_text

        return StatusPage(
            company_name=company_name,
            base_url=normalized,
            has_api=False,
            has_history_pages=True,
        )
