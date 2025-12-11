"""Analyze incidents for trends, statistics, and key issues."""

import logging
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Optional

from ..categorization.ai_client import AIClient
from ..categorization.prompts import KEY_ISSUES_SYSTEM, KEY_ISSUES_USER
from ..models import (
    Category,
    Incident,
    IncidentStats,
    KeyIssue,
    PeerComparison,
    TrendData,
)

logger = logging.getLogger(__name__)


class IncidentAnalyzer:
    """Analyze incidents for statistics, trends, and insights."""

    def __init__(self, ai_client: Optional[AIClient] = None):
        """
        Initialize the analyzer.

        Args:
            ai_client: Optional AI client for generating key issues
        """
        self.ai_client = ai_client

    def calculate_stats(self, incidents: list[Incident]) -> IncidentStats:
        """
        Calculate statistics for a set of incidents.

        Args:
            incidents: List of incidents to analyze

        Returns:
            IncidentStats with calculated metrics
        """
        if not incidents:
            return IncidentStats()

        # Basic counts
        total = len(incidents)
        resolved = sum(1 for i in incidents if i.is_resolved)
        unresolved = total - resolved

        # Count by impact
        critical = sum(1 for i in incidents if i.impact == "critical")
        major = sum(1 for i in incidents if i.impact == "major")
        minor = sum(1 for i in incidents if i.impact == "minor")
        none_impact = sum(
            1 for i in incidents if i.impact in ("none", "maintenance")
        )

        # Count by category
        by_category: dict[str, int] = defaultdict(int)
        for incident in incidents:
            category = incident.category or "uncategorized"
            by_category[category] += 1

        # Duration statistics (only for resolved incidents with duration)
        durations = [
            i.duration_hours
            for i in incidents
            if i.is_resolved and i.duration_hours is not None
        ]

        avg_duration = None
        median_duration = None
        min_duration = None
        max_duration = None
        mttr = None

        if durations:
            avg_duration = statistics.mean(durations)
            median_duration = statistics.median(durations)
            min_duration = min(durations)
            max_duration = max(durations)
            mttr = avg_duration  # MTTR is mean time to resolution

        return IncidentStats(
            total_count=total,
            resolved_count=resolved,
            unresolved_count=unresolved,
            critical_count=critical,
            major_count=major,
            minor_count=minor,
            none_count=none_impact,
            by_category=dict(by_category),
            avg_duration_hours=avg_duration,
            median_duration_hours=median_duration,
            min_duration_hours=min_duration,
            max_duration_hours=max_duration,
            mttr_hours=mttr,
        )

    def calculate_trends(
        self,
        incidents: list[Incident],
        start_date: datetime,
        end_date: datetime,
        period: str = "month",
    ) -> list[TrendData]:
        """
        Calculate incident trends over time.

        Args:
            incidents: List of incidents
            start_date: Start of analysis period
            end_date: End of analysis period
            period: Grouping period ("month" or "quarter")

        Returns:
            List of TrendData for each period
        """
        if not incidents:
            return []

        # Group incidents by period
        grouped: dict[str, list[Incident]] = defaultdict(list)

        for incident in incidents:
            incident_date = incident.started_at or incident.created_at

            if period == "month":
                period_key = incident_date.strftime("%Y-%m")
            elif period == "quarter":
                quarter = (incident_date.month - 1) // 3 + 1
                period_key = f"{incident_date.year}-Q{quarter}"
            else:
                period_key = incident_date.strftime("%Y-%m")

            grouped[period_key].append(incident)

        # Generate trend data for each period
        trends = []
        sorted_periods = sorted(grouped.keys())

        for period_key in sorted_periods:
            period_incidents = grouped[period_key]

            # Calculate period boundaries
            if period == "month":
                year, month = map(int, period_key.split("-"))
                period_start = datetime(year, month, 1)
                if month == 12:
                    period_end = datetime(year + 1, 1, 1)
                else:
                    period_end = datetime(year, month + 1, 1)
            else:
                year, q = period_key.split("-Q")
                quarter = int(q)
                period_start = datetime(int(year), (quarter - 1) * 3 + 1, 1)
                if quarter == 4:
                    period_end = datetime(int(year) + 1, 1, 1)
                else:
                    period_end = datetime(int(year), quarter * 3 + 1, 1)

            # Calculate metrics
            incident_count = len(period_incidents)
            critical_count = sum(
                1 for i in period_incidents if i.impact == "critical"
            )
            major_count = sum(1 for i in period_incidents if i.impact == "major")

            durations = [
                i.duration_hours
                for i in period_incidents
                if i.is_resolved and i.duration_hours is not None
            ]
            avg_duration = statistics.mean(durations) if durations else None

            total_downtime = sum(d for d in durations if d is not None)

            trends.append(
                TrendData(
                    period=period_key,
                    period_start=period_start,
                    period_end=period_end,
                    incident_count=incident_count,
                    critical_count=critical_count,
                    major_count=major_count,
                    avg_duration_hours=avg_duration,
                    total_downtime_hours=total_downtime,
                )
            )

        return trends

    async def identify_key_issues(
        self,
        incidents: list[Incident],
        company_name: str,
        start_date: datetime,
        end_date: datetime,
        categories: list[Category],
    ) -> list[KeyIssue]:
        """
        Use AI to identify key reliability issues from incident data.

        Args:
            incidents: List of incidents
            company_name: Company name
            start_date: Start of analysis period
            end_date: End of analysis period
            categories: List of categories

        Returns:
            List of KeyIssue objects
        """
        if not self.ai_client or not incidents:
            return self._identify_key_issues_heuristic(incidents, categories)

        # Prepare data for AI
        stats = self.calculate_stats(incidents)
        trends = self.calculate_trends(incidents, start_date, end_date)

        # Format incidents by category
        by_category = defaultdict(list)
        for incident in incidents:
            by_category[incident.category or "uncategorized"].append(incident)

        incidents_by_category = []
        for cat_id, cat_incidents in sorted(
            by_category.items(), key=lambda x: -len(x[1])
        ):
            category = next(
                (c for c in categories if c.id == cat_id),
                Category(id=cat_id, name=cat_id, description=""),
            )
            incidents_by_category.append(
                f"- {category.name}: {len(cat_incidents)} incidents"
            )

        # Format trends
        trends_data = []
        for trend in trends[-12:]:  # Last 12 periods
            trends_data.append(
                f"- {trend.period}: {trend.incident_count} incidents, "
                f"{trend.critical_count} critical, "
                f"avg duration {trend.avg_duration_hours:.1f}h"
                if trend.avg_duration_hours
                else f"- {trend.period}: {trend.incident_count} incidents"
            )

        prompt = KEY_ISSUES_USER.format(
            company_name=company_name,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            incidents_by_category="\n".join(incidents_by_category),
            trends_data="\n".join(trends_data) or "No trend data available",
            total_incidents=stats.total_count,
            severe_count=stats.critical_count + stats.major_count,
            avg_resolution_hours=f"{stats.avg_duration_hours:.1f}"
            if stats.avg_duration_hours
            else "N/A",
            mttr_hours=f"{stats.mttr_hours:.1f}" if stats.mttr_hours else "N/A",
        )

        try:
            result = await self.ai_client.generate_json(
                system_prompt=KEY_ISSUES_SYSTEM,
                user_prompt=prompt,
                temperature=0.3,
                max_tokens=2048,
            )

            issues = []
            for item in result:
                issues.append(
                    KeyIssue(
                        issue=item.get("issue", "Unknown issue"),
                        frequency=item.get("frequency", "Unknown"),
                        trend=item.get("trend", "stable"),
                        impact=item.get("impact", "Unknown impact"),
                        recommendation=item.get("recommendation"),
                    )
                )

            return issues

        except Exception as e:
            logger.warning(f"Error generating key issues with AI: {e}")
            return self._identify_key_issues_heuristic(incidents, categories)

    def _identify_key_issues_heuristic(
        self, incidents: list[Incident], categories: list[Category]
    ) -> list[KeyIssue]:
        """
        Identify key issues using simple heuristics (fallback when AI unavailable).

        Args:
            incidents: List of incidents
            categories: List of categories

        Returns:
            List of KeyIssue objects
        """
        if not incidents:
            return []

        issues = []

        # Find most common category
        by_category = defaultdict(list)
        for incident in incidents:
            by_category[incident.category or "uncategorized"].append(incident)

        sorted_categories = sorted(by_category.items(), key=lambda x: -len(x[1]))

        if sorted_categories:
            top_cat, top_incidents = sorted_categories[0]
            category = next(
                (c for c in categories if c.id == top_cat),
                Category(id=top_cat, name=top_cat, description=""),
            )
            issues.append(
                KeyIssue(
                    issue=f"High frequency of {category.name} incidents",
                    frequency=f"{len(top_incidents)} incidents in analysis period",
                    trend="stable",
                    impact=f"Most common incident type ({len(top_incidents)}/{len(incidents)} = {len(top_incidents)/len(incidents)*100:.0f}%)",
                    recommendation=f"Investigate root causes of {category.name.lower()} issues",
                )
            )

        # Find critical incidents
        critical = [i for i in incidents if i.impact == "critical"]
        if critical:
            issues.append(
                KeyIssue(
                    issue="Critical impact incidents",
                    frequency=f"{len(critical)} critical incidents",
                    trend="stable",
                    impact="Critical incidents cause major service disruption",
                    recommendation="Prioritize prevention of critical-impact incidents",
                )
            )

        # Find long-duration incidents
        long_incidents = [
            i for i in incidents if i.duration_hours and i.duration_hours > 2
        ]
        if long_incidents:
            avg_duration = statistics.mean(
                i.duration_hours for i in long_incidents if i.duration_hours
            )
            issues.append(
                KeyIssue(
                    issue="Extended duration incidents",
                    frequency=f"{len(long_incidents)} incidents over 2 hours",
                    trend="stable",
                    impact=f"Average duration of long incidents: {avg_duration:.1f} hours",
                    recommendation="Review incident response procedures to reduce MTTR",
                )
            )

        return issues[:5]  # Return top 5 issues

    def compare_with_peer(
        self,
        target_incidents: list[Incident],
        peer_incidents: list[Incident],
        peer_name: str,
    ) -> PeerComparison:
        """
        Compare target company with a peer.

        Args:
            target_incidents: Target company's incidents
            peer_incidents: Peer company's incidents
            peer_name: Peer company name

        Returns:
            PeerComparison object
        """
        target_stats = self.calculate_stats(target_incidents)
        peer_stats = self.calculate_stats(peer_incidents)

        # Calculate differences
        incident_diff = target_stats.total_count - peer_stats.total_count

        mttr_diff = None
        if target_stats.mttr_hours and peer_stats.mttr_hours:
            mttr_diff = target_stats.mttr_hours - peer_stats.mttr_hours

        return PeerComparison(
            peer_name=peer_name,
            peer_incident_count=peer_stats.total_count,
            peer_mttr_hours=peer_stats.mttr_hours,
            peer_critical_count=peer_stats.critical_count,
            incident_count_diff=incident_diff,
            mttr_diff_hours=mttr_diff,
        )

    def compare_with_peers(
        self,
        target_incidents: list[Incident],
        peer_incidents_map: dict[str, list[Incident]],
    ) -> list[PeerComparison]:
        """
        Compare target company with multiple peers.

        Args:
            target_incidents: Target company's incidents
            peer_incidents_map: Dict of peer name -> incidents

        Returns:
            List of PeerComparison objects
        """
        comparisons = []
        for peer_name, peer_incidents in peer_incidents_map.items():
            comparison = self.compare_with_peer(
                target_incidents, peer_incidents, peer_name
            )
            comparisons.append(comparison)

        return comparisons
