"""Generic scraper for non-Statuspage.io status pages."""

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


class GenericStatusPageScraper(BaseFetcher):
    """
    Generic scraper that attempts to extract incident data from any status page.

    Uses heuristics and common patterns to identify incident information.
    """

    # Common CSS selectors for incident elements
    INCIDENT_SELECTORS = [
        ".incident",
        ".incident-container",
        ".incident-item",
        ".status-incident",
        "[data-incident]",
        ".timeline-item",
        ".event",
        ".outage",
        ".disruption",
        "article.incident",
        ".incident-card",
        ".status-event",
    ]

    # Common selectors for incident titles
    TITLE_SELECTORS = [
        ".incident-title",
        ".incident-name",
        ".title",
        "h3",
        "h4",
        ".event-title",
        ".summary",
        ".headline",
        "a.incident-link",
    ]

    # Common selectors for dates
    DATE_SELECTORS = [
        ".incident-date",
        ".date",
        "time",
        ".timestamp",
        ".datetime",
        "[datetime]",
        ".published",
        ".created",
        "small.text-muted",
    ]

    # Common selectors for status
    STATUS_SELECTORS = [
        ".incident-status",
        ".status",
        ".state",
        ".badge",
        ".label",
        ".tag",
    ]

    # Common selectors for description/body
    BODY_SELECTORS = [
        ".incident-body",
        ".description",
        ".content",
        ".message",
        ".details",
        "p",
        ".update-body",
    ]

    def __init__(self, rate_limit: float = 1.0, timeout: float = 30.0):
        """Initialize the generic scraper."""
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
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _fetch_html(self, url: str) -> str:
        """Fetch HTML from URL."""
        await self.rate_limiter.acquire()
        client = await self._get_client()
        logger.debug(f"Fetching: {url}")
        response = await client.get(url)
        response.raise_for_status()
        return response.text

    def _find_element(self, parent, selectors: list[str]):
        """Find first matching element from list of selectors."""
        for selector in selectors:
            elem = parent.select_one(selector)
            if elem:
                return elem
        return None

    def _find_elements(self, parent, selectors: list[str]) -> list:
        """Find all matching elements from list of selectors."""
        for selector in selectors:
            elems = parent.select(selector)
            if elems:
                return elems
        return []

    def _parse_date(self, text: str) -> datetime | None:
        """Parse date from various text formats."""
        if not text:
            return None

        text = text.strip()

        # Common date patterns
        patterns = [
            (r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", "%Y-%m-%dT%H:%M:%S"),
            (r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})", "%Y-%m-%d %H:%M"),
            (r"(\d{4})-(\d{2})-(\d{2})", "%Y-%m-%d"),
            (r"(\w+) (\d{1,2}),? (\d{4}) (\d{1,2}):(\d{2})", "%B %d %Y %H:%M"),
            (r"(\w+) (\d{1,2}),? (\d{4})", "%B %d %Y"),
            (r"(\d{1,2})/(\d{1,2})/(\d{4})", "%m/%d/%Y"),
            (r"(\d{1,2})-(\w+)-(\d{4})", "%d-%b-%Y"),
        ]

        for pattern, date_format in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    date_str = match.group(0)
                    return datetime.strptime(date_str, date_format)
                except ValueError:
                    continue

        # Try to extract from datetime attribute
        if "datetime" in text.lower():
            match = re.search(r'datetime=["\']([^"\']+)["\']', text)
            if match:
                return self._parse_date(match.group(1))

        return None

    def _extract_impact(self, elem) -> str:
        """Extract impact level from element classes and text."""
        # Check classes
        classes = " ".join(elem.get("class", [])).lower()
        text = elem.get_text().lower()

        combined = classes + " " + text

        if any(word in combined for word in ["critical", "outage", "down", "severe"]):
            return "critical"
        elif any(word in combined for word in ["major", "significant", "degraded"]):
            return "major"
        elif any(word in combined for word in ["minor", "partial", "intermittent"]):
            return "minor"
        elif any(word in combined for word in ["maintenance", "scheduled", "planned"]):
            return "maintenance"

        return "none"

    def _extract_status(self, elem) -> str:
        """Extract status from element."""
        status_elem = self._find_element(elem, self.STATUS_SELECTORS)
        if status_elem:
            text = status_elem.get_text().lower().strip()
            if "investigating" in text:
                return "investigating"
            elif "identified" in text:
                return "identified"
            elif "monitoring" in text:
                return "monitoring"
            elif "resolved" in text or "completed" in text:
                return "resolved"
            elif "scheduled" in text or "planned" in text:
                return "scheduled"

        return "resolved"

    def _parse_incident(
        self, elem, company_name: str, base_url: str, index: int
    ) -> Incident | None:
        """Parse an incident from an HTML element."""
        try:
            # Extract title
            title_elem = self._find_element(elem, self.TITLE_SELECTORS)
            if not title_elem:
                # Try to get text from the element itself
                title = elem.get_text(strip=True)[:200]
                if not title:
                    return None
            else:
                title = title_elem.get_text(strip=True)

            if not title or len(title) < 3:
                return None

            # Extract date
            date_elem = self._find_element(elem, self.DATE_SELECTORS)
            created_at = None
            if date_elem:
                # Try datetime attribute first
                dt_attr = date_elem.get("datetime")
                if dt_attr:
                    created_at = self._parse_date(dt_attr)
                if not created_at:
                    created_at = self._parse_date(date_elem.get_text())

            if not created_at:
                created_at = datetime.now()

            # Extract status and impact
            status = self._extract_status(elem)
            impact = self._extract_impact(elem)

            # Generate ID
            incident_id = f"generic_{hash(title + str(created_at)) & 0xFFFFFFFF:08x}"

            # Extract link if available
            link_elem = elem.select_one("a[href]")
            shortlink = None
            if link_elem:
                href = link_elem.get("href", "")
                if href and not href.startswith("#"):
                    shortlink = urljoin(base_url, href)

            # Extract description
            body_elem = self._find_element(elem, self.BODY_SELECTORS)
            description = ""
            if body_elem:
                description = body_elem.get_text(strip=True)[:500]

            # Create incident updates if we have description
            updates = []
            if description:
                updates.append(
                    IncidentUpdate(
                        id=f"{incident_id}_update_0",
                        status=status,
                        body=description,
                        created_at=created_at,
                    )
                )

            return Incident(
                id=incident_id,
                name=title,
                status=status,
                impact=impact,
                created_at=created_at,
                updated_at=created_at,
                resolved_at=created_at if status == "resolved" else None,
                started_at=created_at,
                incident_updates=updates,
                affected_components=[],
                source_url=base_url,
                company_name=company_name,
                shortlink=shortlink,
            )

        except Exception as e:
            logger.warning(f"Error parsing incident element: {e}")
            return None

    async def _detect_status_page_type(self, html: str, url: str) -> dict:
        """Detect the type of status page and optimal selectors."""
        soup = BeautifulSoup(html, "lxml")

        info = {
            "type": "unknown",
            "incident_selector": None,
            "has_pagination": False,
            "pagination_pattern": None,
        }

        # Check for common status page platforms
        page_text = html.lower()

        if "statuspage.io" in page_text or "atlassian" in page_text:
            info["type"] = "statuspage"
            info["incident_selector"] = ".incident-container"
        elif "status.io" in page_text:
            info["type"] = "status.io"
            info["incident_selector"] = ".incident"
        elif "cachet" in page_text:
            info["type"] = "cachet"
            info["incident_selector"] = ".timeline__item"
        elif "instatus" in page_text:
            info["type"] = "instatus"
            info["incident_selector"] = "[data-testid='incident']"
        elif "betteruptime" in page_text or "better uptime" in page_text:
            info["type"] = "betteruptime"
            info["incident_selector"] = ".incident-item"

        # Check for pagination
        pagination_elem = soup.select_one(
            ".pagination, .pager, nav[aria-label*='pagination'], .page-numbers"
        )
        if pagination_elem:
            info["has_pagination"] = True
            # Try to detect pagination pattern
            next_link = pagination_elem.select_one("a[rel='next'], a.next, a:contains('Next')")
            if next_link:
                info["pagination_pattern"] = next_link.get("href")

        return info

    async def fetch_incidents(
        self,
        base_url: str,
        company_name: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        max_pages: int = 10,
    ) -> list[Incident]:
        """
        Fetch incidents from any status page using heuristics.

        Args:
            base_url: URL of the status page
            company_name: Company name for labeling
            start_date: Start of timeframe
            end_date: End of timeframe
            max_pages: Maximum pages to fetch

        Returns:
            List of Incident objects
        """
        all_incidents = []
        parsed = urlparse(base_url)
        normalized_url = f"{parsed.scheme}://{parsed.netloc}"

        # Common history page paths to try
        history_paths = [
            "/history",
            "/incidents",
            "/status-history",
            "/past-incidents",
            "/updates",
            "",  # Try base URL too
        ]

        for path in history_paths:
            try:
                url = urljoin(normalized_url, path)
                html = await self._fetch_html(url)

                # Detect page type
                page_info = await self._detect_status_page_type(html, url)
                logger.debug(f"Detected page type: {page_info['type']} for {url}")

                soup = BeautifulSoup(html, "lxml")

                # Find incidents using detected selector or try all
                incident_elems = []
                if page_info["incident_selector"]:
                    incident_elems = soup.select(page_info["incident_selector"])

                if not incident_elems:
                    # Try all common selectors
                    incident_elems = self._find_elements(soup, self.INCIDENT_SELECTORS)

                if incident_elems:
                    logger.info(f"Found {len(incident_elems)} potential incidents at {url}")

                    for i, elem in enumerate(incident_elems):
                        incident = self._parse_incident(elem, company_name, normalized_url, i)
                        if incident:
                            all_incidents.append(incident)

                    # If we found incidents, stop trying other paths
                    if all_incidents:
                        break

            except Exception as e:
                logger.debug(f"Error fetching {path}: {e}")
                continue

        # Handle pagination if detected
        # (simplified - just try page=2, page=3, etc.)
        if all_incidents and max_pages > 1:
            for page_num in range(2, max_pages + 1):
                try:
                    page_url = f"{base_url}?page={page_num}"
                    html = await self._fetch_html(page_url)
                    soup = BeautifulSoup(html, "lxml")

                    incident_elems = self._find_elements(soup, self.INCIDENT_SELECTORS)
                    if not incident_elems:
                        break

                    page_incidents = []
                    for i, elem in enumerate(incident_elems):
                        incident = self._parse_incident(
                            elem, company_name, normalized_url, i + len(all_incidents)
                        )
                        if incident:
                            page_incidents.append(incident)

                    if not page_incidents:
                        break

                    all_incidents.extend(page_incidents)

                except Exception:
                    break

        logger.info(f"Fetched {len(all_incidents)} incidents from {company_name} (generic)")

        # Filter and deduplicate
        filtered = self.filter_by_timeframe(all_incidents, start_date, end_date)

        seen_titles = set()
        unique = []
        for incident in filtered:
            if incident.name not in seen_titles:
                seen_titles.add(incident.name)
                unique.append(incident)

        return unique

    async def fetch_status_page_info(self, base_url: str) -> StatusPage:
        """Fetch basic status page info."""
        parsed = urlparse(base_url)
        normalized_url = f"{parsed.scheme}://{parsed.netloc}"

        try:
            html = await self._fetch_html(normalized_url)
            soup = BeautifulSoup(html, "lxml")

            # Try to find company name
            company_name = parsed.netloc
            title_elem = soup.select_one("title, h1, .page-title")
            if title_elem:
                title = title_elem.get_text(strip=True)
                # Clean up title
                for suffix in [" Status", " System Status", " - Status", " | Status"]:
                    if title.endswith(suffix):
                        title = title[:-len(suffix)]
                company_name = title

            return StatusPage(
                company_name=company_name,
                base_url=normalized_url,
                has_api=False,
                has_history_pages=True,
            )

        except Exception as e:
            logger.error(f"Error fetching status page info: {e}")
            return StatusPage(
                company_name=parsed.netloc,
                base_url=normalized_url,
                has_api=False,
            )


class RSSFeedFetcher(BaseFetcher):
    """Fetch incidents from RSS/Atom feeds."""

    def __init__(self, rate_limit: float = 1.0, timeout: float = 30.0):
        """Initialize the RSS fetcher."""
        super().__init__(rate_limit)
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": "ReliabilityReporter/1.0"},
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def fetch_incidents(
        self,
        base_url: str,
        company_name: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[Incident]:
        """Fetch incidents from RSS feed."""
        try:
            import feedparser
        except ImportError:
            logger.warning("feedparser not installed, skipping RSS fetch")
            return []

        # Common RSS feed paths
        feed_paths = [
            "/history.rss",
            "/feed.rss",
            "/rss",
            "/feed",
            "/atom.xml",
            "/incidents.rss",
            "/status.rss",
        ]

        parsed = urlparse(base_url)
        normalized_url = f"{parsed.scheme}://{parsed.netloc}"

        await self.rate_limiter.acquire()
        client = await self._get_client()

        for path in feed_paths:
            try:
                url = urljoin(normalized_url, path)
                response = await client.get(url)

                if response.status_code == 200:
                    feed = feedparser.parse(response.text)

                    if feed.entries:
                        incidents = []
                        for entry in feed.entries:
                            # Parse entry
                            created_at = datetime.now()
                            if hasattr(entry, "published_parsed") and entry.published_parsed:
                                created_at = datetime(*entry.published_parsed[:6])
                            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                                created_at = datetime(*entry.updated_parsed[:6])

                            incident = Incident(
                                id=entry.get("id", f"rss_{hash(entry.title) & 0xFFFFFFFF:08x}"),
                                name=entry.get("title", "Unknown"),
                                status="resolved",
                                impact="none",
                                created_at=created_at,
                                updated_at=created_at,
                                resolved_at=created_at,
                                started_at=created_at,
                                incident_updates=[
                                    IncidentUpdate(
                                        id="rss_update",
                                        status="resolved",
                                        body=entry.get("summary", ""),
                                        created_at=created_at,
                                    )
                                ] if entry.get("summary") else [],
                                affected_components=[],
                                source_url=normalized_url,
                                company_name=company_name,
                                shortlink=entry.get("link"),
                            )
                            incidents.append(incident)

                        logger.info(f"Fetched {len(incidents)} incidents from RSS feed")
                        return self.filter_by_timeframe(incidents, start_date, end_date)

            except Exception as e:
                logger.debug(f"Error fetching RSS from {path}: {e}")
                continue

        return []

    async def fetch_status_page_info(self, base_url: str) -> StatusPage:
        """Fetch basic status page info from RSS."""
        parsed = urlparse(base_url)
        return StatusPage(
            company_name=parsed.netloc,
            base_url=f"{parsed.scheme}://{parsed.netloc}",
            has_api=False,
            has_rss=True,
        )
