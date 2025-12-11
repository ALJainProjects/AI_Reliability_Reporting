"""Generate incident categories from cross-company analysis."""

import json
import logging
from collections import defaultdict

from ..models import Category, Incident
from .ai_client import AIClient
from .prompts import CATEGORY_GENERATION_SYSTEM, CATEGORY_GENERATION_USER

logger = logging.getLogger(__name__)

# Default categories as fallback
DEFAULT_CATEGORIES = [
    Category(
        id="database-storage",
        name="Database/Storage Issues",
        description="Database outages, connection failures, storage system issues, data access problems",
        keywords=["database", "db", "postgres", "mysql", "mongodb", "redis", "storage", "disk", "connection pool"],
    ),
    Category(
        id="network-connectivity",
        name="Network/Connectivity Issues",
        description="Network outages, connectivity problems, routing issues, DNS failures",
        keywords=["network", "connectivity", "dns", "routing", "latency", "packet loss", "connection"],
    ),
    Category(
        id="authentication-authorization",
        name="Authentication/Authorization",
        description="Login failures, authentication issues, authorization problems, SSO issues",
        keywords=["auth", "login", "sso", "oauth", "permission", "access denied", "token"],
    ),
    Category(
        id="api-service-degradation",
        name="API/Service Degradation",
        description="API errors, service degradation, increased latency, partial outages",
        keywords=["api", "service", "degraded", "slow", "timeout", "error rate", "5xx"],
    ),
    Category(
        id="deployment-release",
        name="Deployment/Release Issues",
        description="Deployment failures, release rollbacks, configuration changes causing issues",
        keywords=["deploy", "release", "rollback", "config", "update", "migration"],
    ),
    Category(
        id="third-party-dependency",
        name="Third-Party Dependencies",
        description="Issues with external services, vendor outages, integration failures",
        keywords=["third-party", "vendor", "external", "integration", "provider", "upstream"],
    ),
    Category(
        id="infrastructure-cloud",
        name="Infrastructure/Cloud Provider",
        description="Cloud provider issues, infrastructure failures, compute/memory problems",
        keywords=["aws", "gcp", "azure", "cloud", "infrastructure", "server", "vm", "container"],
    ),
    Category(
        id="performance-capacity",
        name="Performance/Capacity",
        description="Performance degradation, capacity limits, resource exhaustion",
        keywords=["performance", "capacity", "scaling", "load", "cpu", "memory", "throughput"],
    ),
    Category(
        id="scheduled-maintenance",
        name="Scheduled Maintenance",
        description="Planned maintenance windows, scheduled updates, announced downtime",
        keywords=["maintenance", "scheduled", "planned", "upgrade", "update window"],
    ),
    Category(
        id="other",
        name="Other",
        description="Incidents that don't fit into other categories",
        keywords=[],
    ),
]


class CategoryGenerator:
    """Generate incident categories using AI analysis."""

    def __init__(self, ai_client: AIClient, max_sample_per_company: int = 30):
        """
        Initialize the category generator.

        Args:
            ai_client: AI client for generation
            max_sample_per_company: Maximum incidents to sample per company for analysis
        """
        self.ai_client = ai_client
        self.max_sample_per_company = max_sample_per_company

    def _prepare_incidents_sample(
        self, incidents: list[Incident]
    ) -> tuple[str, list[str]]:
        """
        Prepare a representative sample of incidents for the AI prompt.

        Args:
            incidents: All incidents from all companies

        Returns:
            Tuple of (formatted sample string, list of company names)
        """
        # Group incidents by company
        by_company: dict[str, list[Incident]] = defaultdict(list)
        for incident in incidents:
            by_company[incident.company_name].append(incident)

        company_names = list(by_company.keys())

        # Sample incidents from each company
        sample_parts = []
        for company_name, company_incidents in by_company.items():
            # Sort by date (newest first) and take sample
            sorted_incidents = sorted(
                company_incidents,
                key=lambda x: x.created_at,
                reverse=True,
            )
            sample = sorted_incidents[: self.max_sample_per_company]

            sample_parts.append(f"\n### {company_name} ({len(company_incidents)} total incidents)")

            for incident in sample:
                # Get first update body if available
                first_update = ""
                if incident.incident_updates:
                    sorted_updates = sorted(
                        incident.incident_updates, key=lambda x: x.created_at
                    )
                    first_update = sorted_updates[0].body[:200] if sorted_updates else ""

                components = ", ".join(c.name for c in incident.affected_components[:3])

                sample_parts.append(
                    f"- [{incident.impact.upper()}] {incident.name}"
                    + (f"\n  Components: {components}" if components else "")
                    + (f"\n  Details: {first_update}..." if first_update else "")
                )

        return "\n".join(sample_parts), company_names

    async def generate_categories(
        self, incidents: list[Incident], use_default_on_error: bool = True
    ) -> list[Category]:
        """
        Generate incident categories by analyzing incidents from all companies.

        Args:
            incidents: All incidents from all companies
            use_default_on_error: Return default categories if AI generation fails

        Returns:
            List of Category objects
        """
        if not incidents:
            logger.warning("No incidents provided, using default categories")
            return DEFAULT_CATEGORIES

        logger.info(f"Generating categories from {len(incidents)} incidents")

        # Prepare sample for AI
        incidents_sample, company_names = self._prepare_incidents_sample(incidents)

        # Build prompt
        prompt = CATEGORY_GENERATION_USER.format(
            company_list="\n".join(f"- {name}" for name in company_names),
            total_incidents=len(incidents),
            incidents_sample=incidents_sample,
        )

        try:
            # Generate categories using AI
            result = await self.ai_client.generate_json(
                system_prompt=CATEGORY_GENERATION_SYSTEM,
                user_prompt=prompt,
                temperature=0.3,
                max_tokens=4096,
            )

            # Parse and validate categories
            categories = self._parse_categories(result)

            # Ensure we have an "other" category
            if not any(c.id == "other" for c in categories):
                categories.append(
                    Category(
                        id="other",
                        name="Other",
                        description="Incidents that don't fit into other categories",
                        keywords=[],
                    )
                )

            logger.info(f"Generated {len(categories)} categories")
            return categories

        except Exception as e:
            logger.error(f"Error generating categories: {e}")
            if use_default_on_error:
                logger.info("Using default categories as fallback")
                return DEFAULT_CATEGORIES
            raise

    def _parse_categories(self, data: list | dict) -> list[Category]:
        """
        Parse AI response into Category objects.

        Args:
            data: Parsed JSON from AI response

        Returns:
            List of validated Category objects
        """
        if isinstance(data, dict):
            # Handle case where response is wrapped
            if "categories" in data:
                data = data["categories"]
            else:
                data = [data]

        categories = []
        for item in data:
            try:
                category = Category(
                    id=item.get("id", "").lower().replace(" ", "-"),
                    name=item.get("name", "Unknown"),
                    description=item.get("description", ""),
                    keywords=item.get("keywords", []),
                )
                categories.append(category)
            except Exception as e:
                logger.warning(f"Error parsing category: {e}, data: {item}")
                continue

        return categories

    def get_default_categories(self) -> list[Category]:
        """Get the default category set."""
        return DEFAULT_CATEGORIES.copy()

    def categories_to_json(self, categories: list[Category]) -> str:
        """
        Convert categories to JSON string for prompts.

        Args:
            categories: List of categories

        Returns:
            JSON string representation
        """
        return json.dumps(
            [
                {
                    "id": c.id,
                    "name": c.name,
                    "description": c.description,
                    "keywords": c.keywords,
                }
                for c in categories
            ],
            indent=2,
        )
