"""Custom category training and fine-tuning."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..models import Category, Incident
from .ai_client import AIClient

logger = logging.getLogger(__name__)


class CategoryTrainer:
    """
    Train and fine-tune incident categories based on user feedback
    and historical incident data.
    """

    def __init__(
        self,
        ai_client: Optional[AIClient] = None,
        training_data_dir: Path = Path("./training_data"),
    ):
        """
        Initialize the category trainer.

        Args:
            ai_client: AI client for generating improved categories
            training_data_dir: Directory to store training data
        """
        self.ai_client = ai_client
        self.training_data_dir = Path(training_data_dir)
        self.training_data_dir.mkdir(parents=True, exist_ok=True)

        self.feedback_file = self.training_data_dir / "category_feedback.json"
        self.custom_rules_file = self.training_data_dir / "custom_rules.json"
        self.trained_categories_file = self.training_data_dir / "trained_categories.json"

    def load_feedback(self) -> list[dict]:
        """Load stored feedback data."""
        if self.feedback_file.exists():
            return json.loads(self.feedback_file.read_text())
        return []

    def save_feedback(self, feedback: list[dict]) -> None:
        """Save feedback data."""
        self.feedback_file.write_text(json.dumps(feedback, indent=2))

    def add_feedback(
        self,
        incident_id: str,
        incident_title: str,
        original_category: str,
        corrected_category: str,
        user_notes: str | None = None,
    ) -> None:
        """
        Add user feedback for category correction.

        Args:
            incident_id: ID of the incident
            incident_title: Title of the incident
            original_category: AI-assigned category
            corrected_category: User-corrected category
            user_notes: Optional notes about the correction
        """
        feedback = self.load_feedback()
        feedback.append({
            "incident_id": incident_id,
            "incident_title": incident_title,
            "original_category": original_category,
            "corrected_category": corrected_category,
            "user_notes": user_notes,
            "timestamp": datetime.now().isoformat(),
        })
        self.save_feedback(feedback)
        logger.info(f"Added feedback for incident {incident_id}: {original_category} -> {corrected_category}")

    def load_custom_rules(self) -> dict:
        """Load custom classification rules."""
        if self.custom_rules_file.exists():
            return json.loads(self.custom_rules_file.read_text())
        return {
            "keyword_mappings": {},  # keyword -> category_id
            "title_patterns": [],     # regex patterns with category mappings
            "component_mappings": {}, # component name -> category_id
        }

    def save_custom_rules(self, rules: dict) -> None:
        """Save custom classification rules."""
        self.custom_rules_file.write_text(json.dumps(rules, indent=2))

    def add_keyword_rule(self, keyword: str, category_id: str) -> None:
        """
        Add a keyword-to-category mapping rule.

        Args:
            keyword: Keyword to match (case-insensitive)
            category_id: Category to assign when keyword is found
        """
        rules = self.load_custom_rules()
        rules["keyword_mappings"][keyword.lower()] = category_id
        self.save_custom_rules(rules)
        logger.info(f"Added keyword rule: '{keyword}' -> {category_id}")

    def add_component_rule(self, component_name: str, category_id: str) -> None:
        """
        Add a component-to-category mapping rule.

        Args:
            component_name: Component name to match
            category_id: Category to assign
        """
        rules = self.load_custom_rules()
        rules["component_mappings"][component_name.lower()] = category_id
        self.save_custom_rules(rules)
        logger.info(f"Added component rule: '{component_name}' -> {category_id}")

    def add_title_pattern(self, pattern: str, category_id: str, priority: int = 0) -> None:
        """
        Add a regex pattern rule for title matching.

        Args:
            pattern: Regex pattern to match against incident titles
            category_id: Category to assign when pattern matches
            priority: Higher priority rules are checked first
        """
        rules = self.load_custom_rules()
        rules["title_patterns"].append({
            "pattern": pattern,
            "category_id": category_id,
            "priority": priority,
        })
        # Sort by priority (descending)
        rules["title_patterns"].sort(key=lambda x: -x["priority"])
        self.save_custom_rules(rules)
        logger.info(f"Added title pattern rule: '{pattern}' -> {category_id}")

    def apply_custom_rules(self, incident: Incident) -> str | None:
        """
        Apply custom rules to classify an incident.

        Args:
            incident: Incident to classify

        Returns:
            Category ID if a rule matches, None otherwise
        """
        import re

        rules = self.load_custom_rules()
        text = (incident.name + " " + incident.get_full_description()).lower()

        # Check keyword mappings
        for keyword, category_id in rules.get("keyword_mappings", {}).items():
            if keyword in text:
                return category_id

        # Check component mappings
        for component in incident.affected_components:
            comp_name = component.name.lower()
            if comp_name in rules.get("component_mappings", {}):
                return rules["component_mappings"][comp_name]

        # Check title patterns
        for pattern_rule in rules.get("title_patterns", []):
            try:
                if re.search(pattern_rule["pattern"], incident.name, re.IGNORECASE):
                    return pattern_rule["category_id"]
            except re.error:
                logger.warning(f"Invalid regex pattern: {pattern_rule['pattern']}")

        return None

    def load_trained_categories(self) -> list[Category] | None:
        """Load trained/fine-tuned categories."""
        if self.trained_categories_file.exists():
            data = json.loads(self.trained_categories_file.read_text())
            return [Category.model_validate(c) for c in data]
        return None

    def save_trained_categories(self, categories: list[Category]) -> None:
        """Save trained categories."""
        data = [c.model_dump() for c in categories]
        self.trained_categories_file.write_text(json.dumps(data, indent=2))

    async def train_categories(
        self,
        base_categories: list[Category],
        incidents: list[Incident],
        min_feedback_count: int = 5,
    ) -> list[Category]:
        """
        Train/improve categories based on feedback and incident data.

        Args:
            base_categories: Starting categories
            incidents: Historical incidents
            min_feedback_count: Minimum feedback entries to trigger training

        Returns:
            Improved list of categories
        """
        feedback = self.load_feedback()

        if len(feedback) < min_feedback_count:
            logger.info(f"Not enough feedback ({len(feedback)} < {min_feedback_count}), using base categories")
            return base_categories

        if not self.ai_client:
            logger.info("No AI client, using rule-based improvements only")
            return self._apply_feedback_rules(base_categories, feedback)

        # Use AI to improve categories based on feedback
        improved = await self._ai_improve_categories(base_categories, feedback, incidents)
        self.save_trained_categories(improved)
        return improved

    def _apply_feedback_rules(
        self,
        categories: list[Category],
        feedback: list[dict],
    ) -> list[Category]:
        """Apply feedback to improve category keywords."""
        # Count corrections
        correction_counts: dict[str, dict[str, int]] = {}
        for entry in feedback:
            original = entry["original_category"]
            corrected = entry["corrected_category"]

            if original not in correction_counts:
                correction_counts[original] = {}
            if corrected not in correction_counts[original]:
                correction_counts[original][corrected] = 0
            correction_counts[original][corrected] += 1

        # Extract keywords from corrected incidents
        category_new_keywords: dict[str, set[str]] = {c.id: set() for c in categories}

        for entry in feedback:
            title_words = entry["incident_title"].lower().split()
            corrected = entry["corrected_category"]
            if corrected in category_new_keywords:
                # Add significant words (length > 3, not common words)
                common_words = {"the", "and", "for", "from", "with", "this", "that", "has", "was", "are", "been"}
                for word in title_words:
                    if len(word) > 3 and word not in common_words:
                        category_new_keywords[corrected].add(word)

        # Update categories with new keywords
        improved = []
        for category in categories:
            new_keywords = list(set(category.keywords) | category_new_keywords.get(category.id, set()))
            improved.append(Category(
                id=category.id,
                name=category.name,
                description=category.description,
                keywords=new_keywords[:50],  # Limit keywords
                incident_count=category.incident_count,
            ))

        return improved

    async def _ai_improve_categories(
        self,
        categories: list[Category],
        feedback: list[dict],
        incidents: list[Incident],
    ) -> list[Category]:
        """Use AI to improve categories based on feedback."""
        # Format feedback for prompt
        feedback_summary = []
        for entry in feedback:
            feedback_summary.append(
                f"- '{entry['incident_title']}': {entry['original_category']} -> {entry['corrected_category']}"
                + (f" (Note: {entry['user_notes']})" if entry.get('user_notes') else "")
            )

        # Format current categories
        categories_json = json.dumps([
            {"id": c.id, "name": c.name, "description": c.description, "keywords": c.keywords}
            for c in categories
        ], indent=2)

        prompt = f"""Based on user feedback, improve the incident categories.

Current categories:
{categories_json}

User corrections (original -> corrected):
{chr(10).join(feedback_summary[:50])}

Analyze the feedback patterns and:
1. Improve category descriptions to be more accurate
2. Add relevant keywords that would have helped correct classification
3. Suggest if any categories should be split or merged (but keep the structure similar)

Return improved categories in JSON format:
```json
[
  {{"id": "...", "name": "...", "description": "...", "keywords": [...]}}
]
```

Only return the JSON array, no additional text."""

        system = "You are an expert at incident classification and taxonomy design. Improve categories based on user feedback."

        try:
            result = await self.ai_client.generate_json(
                system_prompt=system,
                user_prompt=prompt,
                temperature=0.3,
            )

            improved = []
            for item in result:
                improved.append(Category(
                    id=item["id"],
                    name=item["name"],
                    description=item["description"],
                    keywords=item.get("keywords", []),
                ))

            return improved

        except Exception as e:
            logger.error(f"Error in AI category improvement: {e}")
            return self._apply_feedback_rules(categories, feedback)

    def create_custom_category(
        self,
        category_id: str,
        name: str,
        description: str,
        keywords: list[str] | None = None,
    ) -> Category:
        """
        Create a new custom category.

        Args:
            category_id: Unique ID for the category
            name: Display name
            description: What incidents belong in this category
            keywords: Keywords for matching

        Returns:
            New Category object
        """
        category = Category(
            id=category_id,
            name=name,
            description=description,
            keywords=keywords or [],
        )

        # Add to trained categories
        trained = self.load_trained_categories() or []
        trained.append(category)
        self.save_trained_categories(trained)

        logger.info(f"Created custom category: {category_id}")
        return category

    def export_training_data(self, output_path: Path) -> None:
        """Export all training data for backup or sharing."""
        data = {
            "feedback": self.load_feedback(),
            "custom_rules": self.load_custom_rules(),
            "trained_categories": [c.model_dump() for c in (self.load_trained_categories() or [])],
            "exported_at": datetime.now().isoformat(),
        }
        output_path.write_text(json.dumps(data, indent=2))
        logger.info(f"Exported training data to {output_path}")

    def import_training_data(self, input_path: Path) -> None:
        """Import training data from a backup."""
        data = json.loads(input_path.read_text())

        if "feedback" in data:
            self.save_feedback(data["feedback"])
        if "custom_rules" in data:
            self.save_custom_rules(data["custom_rules"])
        if "trained_categories" in data:
            categories = [Category.model_validate(c) for c in data["trained_categories"]]
            self.save_trained_categories(categories)

        logger.info(f"Imported training data from {input_path}")

    def get_training_stats(self) -> dict:
        """Get statistics about training data."""
        feedback = self.load_feedback()
        rules = self.load_custom_rules()
        trained = self.load_trained_categories()

        # Analyze feedback
        corrections_by_category: dict[str, int] = {}
        for entry in feedback:
            original = entry["original_category"]
            corrections_by_category[original] = corrections_by_category.get(original, 0) + 1

        return {
            "total_feedback_entries": len(feedback),
            "keyword_rules": len(rules.get("keyword_mappings", {})),
            "component_rules": len(rules.get("component_mappings", {})),
            "title_pattern_rules": len(rules.get("title_patterns", [])),
            "trained_categories": len(trained) if trained else 0,
            "most_corrected_categories": sorted(
                corrections_by_category.items(),
                key=lambda x: -x[1]
            )[:5],
        }
