"""Classify incidents into categories using AI."""

import asyncio
import logging
from datetime import datetime

from ..models import Category, Incident
from .ai_client import AIClient
from .prompts import (
    BATCH_CLASSIFICATION_SYSTEM,
    BATCH_CLASSIFICATION_USER,
    INCIDENT_CLASSIFICATION_SYSTEM,
    INCIDENT_CLASSIFICATION_USER,
)

logger = logging.getLogger(__name__)


class IncidentClassifier:
    """Classify incidents into predefined categories."""

    def __init__(
        self,
        ai_client: AIClient,
        categories: list[Category],
        batch_size: int = 10,
        max_concurrent: int = 5,
    ):
        """
        Initialize the incident classifier.

        Args:
            ai_client: AI client for classification
            categories: List of categories to classify into
            batch_size: Number of incidents per batch (for batch classification)
            max_concurrent: Maximum concurrent API calls
        """
        self.ai_client = ai_client
        self.categories = categories
        self.batch_size = batch_size
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def _format_categories_json(self) -> str:
        """Format categories as JSON for prompts."""
        import json

        return json.dumps(
            [
                {
                    "id": c.id,
                    "name": c.name,
                    "description": c.description,
                }
                for c in self.categories
            ],
            indent=2,
        )

    def _format_incident_updates(self, incident: Incident) -> str:
        """Format incident updates for the prompt."""
        if not incident.incident_updates:
            return "No updates available."

        updates = sorted(incident.incident_updates, key=lambda x: x.created_at)
        parts = []
        for update in updates[:5]:  # Limit to first 5 updates
            timestamp = update.created_at.strftime("%Y-%m-%d %H:%M")
            parts.append(f"[{timestamp}] [{update.status}] {update.body[:300]}")

        return "\n".join(parts)

    def _format_components(self, incident: Incident) -> str:
        """Format affected components for the prompt."""
        if not incident.affected_components:
            return "None specified"

        return ", ".join(c.name for c in incident.affected_components[:10])

    async def classify_incident(self, incident: Incident) -> Incident:
        """
        Classify a single incident.

        Args:
            incident: Incident to classify

        Returns:
            Incident with category, summary, and root_cause populated
        """
        async with self._semaphore:
            prompt = INCIDENT_CLASSIFICATION_USER.format(
                categories_json=self._format_categories_json(),
                incident_title=incident.name,
                incident_impact=incident.impact,
                incident_status=incident.status,
                incident_date=incident.created_at.strftime("%Y-%m-%d %H:%M"),
                incident_updates=self._format_incident_updates(incident),
                affected_components=self._format_components(incident),
            )

            try:
                result = await self.ai_client.generate_json(
                    system_prompt=INCIDENT_CLASSIFICATION_SYSTEM,
                    user_prompt=prompt,
                    temperature=0.2,
                    max_tokens=1024,
                )

                # Update incident with classification results
                incident.category = result.get("category_id", "other")
                incident.category_confidence = result.get("confidence", 0.5)
                incident.summary = result.get("summary")
                incident.root_cause = result.get("root_cause")

                logger.debug(
                    f"Classified incident {incident.id} as {incident.category}"
                )

            except Exception as e:
                logger.warning(f"Error classifying incident {incident.id}: {e}")
                # Default to "other" on error
                incident.category = "other"
                incident.category_confidence = 0.0

            return incident

    async def classify_batch(self, incidents: list[Incident]) -> list[Incident]:
        """
        Classify a batch of incidents in a single API call.

        Args:
            incidents: Batch of incidents to classify

        Returns:
            Incidents with classifications populated
        """
        import json

        # Format incidents for batch prompt
        incidents_batch = []
        for incident in incidents:
            incidents_batch.append(
                {
                    "id": incident.id,
                    "title": incident.name,
                    "impact": incident.impact,
                    "status": incident.status,
                    "date": incident.created_at.strftime("%Y-%m-%d"),
                    "updates": self._format_incident_updates(incident)[:500],
                    "components": self._format_components(incident),
                }
            )

        prompt = BATCH_CLASSIFICATION_USER.format(
            categories_json=self._format_categories_json(),
            incidents_batch=json.dumps(incidents_batch, indent=2),
        )

        try:
            result = await self.ai_client.generate_json(
                system_prompt=BATCH_CLASSIFICATION_SYSTEM,
                user_prompt=prompt,
                temperature=0.2,
                max_tokens=4096,
            )

            # Map results back to incidents
            result_map = {item["incident_id"]: item for item in result}

            for incident in incidents:
                if incident.id in result_map:
                    classification = result_map[incident.id]
                    incident.category = classification.get("category_id", "other")
                    incident.category_confidence = classification.get("confidence", 0.5)
                    incident.summary = classification.get("summary")
                else:
                    incident.category = "other"
                    incident.category_confidence = 0.0

        except Exception as e:
            logger.warning(f"Error in batch classification: {e}")
            # Default all to "other" on error
            for incident in incidents:
                incident.category = "other"
                incident.category_confidence = 0.0

        return incidents

    async def classify_all(
        self, incidents: list[Incident], use_batch: bool = True
    ) -> list[Incident]:
        """
        Classify all incidents.

        Args:
            incidents: All incidents to classify
            use_batch: Use batch classification (more efficient but slightly less accurate)

        Returns:
            All incidents with classifications populated
        """
        if not incidents:
            return []

        logger.info(f"Classifying {len(incidents)} incidents")
        start_time = datetime.now()

        if use_batch:
            # Process in batches
            classified = []
            for i in range(0, len(incidents), self.batch_size):
                batch = incidents[i : i + self.batch_size]
                logger.debug(f"Processing batch {i // self.batch_size + 1}")

                async with self._semaphore:
                    result = await self.classify_batch(batch)
                    classified.extend(result)

                # Small delay between batches
                if i + self.batch_size < len(incidents):
                    await asyncio.sleep(0.5)
        else:
            # Process individually (concurrent)
            tasks = [self.classify_incident(incident) for incident in incidents]
            classified = await asyncio.gather(*tasks)

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"Classified {len(classified)} incidents in {elapsed:.1f}s")

        # Update category counts
        category_counts: dict[str, int] = {}
        for incident in classified:
            if incident.category:
                category_counts[incident.category] = (
                    category_counts.get(incident.category, 0) + 1
                )

        for category in self.categories:
            category.incident_count = category_counts.get(category.id, 0)

        return list(classified)

    def get_category_by_id(self, category_id: str) -> Category | None:
        """Get a category by its ID."""
        for category in self.categories:
            if category.id == category_id:
                return category
        return None
