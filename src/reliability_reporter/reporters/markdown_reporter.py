"""Generate markdown reliability reports."""

import logging
from datetime import datetime
from pathlib import Path

from ..models import Report

logger = logging.getLogger(__name__)


class MarkdownReporter:
    """Generate markdown format reliability reports."""

    def __init__(self):
        """Initialize the markdown reporter."""
        pass

    def generate(self, report: Report) -> str:
        """
        Generate a markdown report from the report data.

        Args:
            report: Report object with all data

        Returns:
            Markdown formatted string
        """
        sections = [
            self._generate_header(report),
            self._generate_executive_summary(report),
            self._generate_statistics(report),
            self._generate_category_breakdown(report),
            self._generate_trends(report),
            self._generate_key_issues(report),
            self._generate_peer_comparison(report),
            self._generate_category_definitions(report),
            self._generate_footer(report),
        ]

        return "\n\n".join(filter(None, sections))

    def _generate_header(self, report: Report) -> str:
        """Generate report header."""
        return f"""# Reliability Report: {report.company_name}

**Analysis Period:** {report.start_date.strftime('%B %d, %Y')} - {report.end_date.strftime('%B %d, %Y')} ({report.timeframe_days} days)

**Generated:** {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')}

**Peer Companies Analyzed:** {', '.join(report.peer_companies) if report.peer_companies else 'None'}

---"""

    def _generate_executive_summary(self, report: Report) -> str:
        """Generate executive summary section."""
        stats = report.stats

        # Determine severity level
        if stats.critical_count > 0:
            severity = "critical"
            severity_emoji = ""
        elif stats.major_count > 3:
            severity = "elevated"
            severity_emoji = ""
        else:
            severity = "normal"
            severity_emoji = ""

        # Calculate incident rate
        incidents_per_month = (
            stats.total_count / (report.timeframe_days / 30)
            if report.timeframe_days > 0
            else 0
        )

        # Format MTTR
        mttr_str = f"{stats.mttr_hours:.1f} hours" if stats.mttr_hours else "N/A"

        if stats.total_count > 0:
            summary = f"""## Executive Summary

### Overview
- **Total Incidents:** {stats.total_count}
- **Incident Rate:** {incidents_per_month:.1f} incidents/month
- **Severity Level:** {severity.title()} {severity_emoji}
- **Mean Time to Resolution (MTTR):** {mttr_str}

### Impact Distribution
| Impact Level | Count | Percentage |
|-------------|-------|------------|
| Critical | {stats.critical_count} | {stats.critical_count/stats.total_count*100:.1f}% |
| Major | {stats.major_count} | {stats.major_count/stats.total_count*100:.1f}% |
| Minor | {stats.minor_count} | {stats.minor_count/stats.total_count*100:.1f}% |
| None/Maintenance | {stats.none_count} | {stats.none_count/stats.total_count*100:.1f}% |"""
        else:
            summary = """## Executive Summary

No incidents recorded during the analysis period."""

        return summary

    def _generate_statistics(self, report: Report) -> str:
        """Generate detailed statistics section."""
        stats = report.stats

        if stats.total_count == 0:
            return ""

        return f"""## Detailed Statistics

### Resolution Metrics
- **Resolved Incidents:** {stats.resolved_count} ({stats.resolved_count/stats.total_count*100:.1f}%)
- **Unresolved Incidents:** {stats.unresolved_count}

### Duration Analysis
| Metric | Value |
|--------|-------|
| Average Duration | {stats.avg_duration_hours:.2f} hours |
| Median Duration | {stats.median_duration_hours:.2f} hours |
| Minimum Duration | {stats.min_duration_hours:.2f} hours |
| Maximum Duration | {stats.max_duration_hours:.2f} hours |""" if stats.avg_duration_hours else f"""## Detailed Statistics

### Resolution Metrics
- **Resolved Incidents:** {stats.resolved_count} ({stats.resolved_count/stats.total_count*100:.1f}%)
- **Unresolved Incidents:** {stats.unresolved_count}

*Duration data not available for all incidents.*"""

    def _generate_category_breakdown(self, report: Report) -> str:
        """Generate incident category breakdown."""
        if not report.stats.by_category:
            return ""

        rows = []
        sorted_categories = sorted(
            report.stats.by_category.items(), key=lambda x: -x[1]
        )

        for cat_id, count in sorted_categories:
            category = next(
                (c for c in report.categories if c.id == cat_id), None
            )
            name = category.name if category else cat_id.replace("-", " ").title()
            percentage = count / report.stats.total_count * 100
            rows.append(f"| {name} | {count} | {percentage:.1f}% |")

        return f"""## Incidents by Category

| Category | Count | Percentage |
|----------|-------|------------|
{chr(10).join(rows)}"""

    def _generate_trends(self, report: Report) -> str:
        """Generate trends section."""
        if not report.trends:
            return ""

        rows = []
        for trend in report.trends:
            duration_str = (
                f"{trend.avg_duration_hours:.1f}h"
                if trend.avg_duration_hours
                else "N/A"
            )
            rows.append(
                f"| {trend.period} | {trend.incident_count} | "
                f"{trend.critical_count} | {trend.major_count} | "
                f"{duration_str} | {trend.total_downtime_hours:.1f}h |"
            )

        return f"""## Monthly Trends

| Period | Incidents | Critical | Major | Avg Duration | Total Downtime |
|--------|-----------|----------|-------|--------------|----------------|
{chr(10).join(rows)}"""

    def _generate_key_issues(self, report: Report) -> str:
        """Generate key issues section."""
        if not report.key_issues:
            return ""

        issues_md = []
        for i, issue in enumerate(report.key_issues, 1):
            trend_indicator = {
                "improving": "(improving)",
                "stable": "(stable)",
                "worsening": "(worsening)",
            }.get(issue.trend, "")

            issues_md.append(
                f"""### {i}. {issue.issue} {trend_indicator}

- **Frequency:** {issue.frequency}
- **Impact:** {issue.impact}
- **Recommendation:** {issue.recommendation or 'N/A'}"""
            )

        return f"""## Key Reliability Issues

{chr(10).join(issues_md)}"""

    def _generate_peer_comparison(self, report: Report) -> str:
        """Generate peer comparison section."""
        if not report.peer_comparisons:
            return ""

        rows = []
        for comp in report.peer_comparisons:
            diff_str = (
                f"+{comp.incident_count_diff}"
                if comp.incident_count_diff > 0
                else str(comp.incident_count_diff)
            )
            mttr_str = (
                f"{comp.peer_mttr_hours:.1f}h" if comp.peer_mttr_hours else "N/A"
            )
            mttr_diff_str = ""
            if comp.mttr_diff_hours is not None:
                mttr_diff_str = (
                    f" ({'+' if comp.mttr_diff_hours > 0 else ''}{comp.mttr_diff_hours:.1f}h)"
                )

            rows.append(
                f"| {comp.peer_name} | {comp.peer_incident_count} | "
                f"{diff_str} | {mttr_str}{mttr_diff_str} | {comp.peer_critical_count} |"
            )

        return f"""## Peer Comparison

| Company | Incidents | vs {report.company_name} | MTTR | Critical |
|---------|-----------|--------------------------|------|----------|
| **{report.company_name}** | **{report.stats.total_count}** | - | **{report.stats.mttr_hours:.1f}h** | **{report.stats.critical_count}** |
{chr(10).join(rows)}

*Positive difference means {report.company_name} has more incidents than the peer.*""" if report.stats.mttr_hours else f"""## Peer Comparison

| Company | Incidents | vs {report.company_name} | MTTR | Critical |
|---------|-----------|--------------------------|------|----------|
| **{report.company_name}** | **{report.stats.total_count}** | - | N/A | **{report.stats.critical_count}** |
{chr(10).join(rows)}"""

    def _generate_category_definitions(self, report: Report) -> str:
        """Generate category definitions section."""
        if not report.categories:
            return ""

        definitions = []
        for cat in report.categories:
            if cat.incident_count == 0:
                continue

            examples = []
            for incident in report.incidents:
                if incident.category == cat.id and len(examples) < 3:
                    examples.append(f"  - {incident.name[:80]}...")

            examples_str = "\n".join(examples) if examples else "  - No examples"

            definitions.append(
                f"""### {cat.name}
**ID:** `{cat.id}`

{cat.description}

**Keywords:** {', '.join(cat.keywords[:10]) if cat.keywords else 'N/A'}

**Example Incidents:**
{examples_str}"""
            )

        return f"""## Category Definitions

{chr(10).join(definitions)}"""

    def _generate_footer(self, report: Report) -> str:
        """Generate report footer."""
        return f"""---

*This report was generated automatically by the AI Reliability Report Generator.*
*Data source: Company status pages*
*Analysis includes {report.total_incidents} incidents from {report.company_name}.*"""

    def save(self, report: Report, output_path: Path | str) -> Path:
        """
        Save the report to a file.

        Args:
            report: Report object
            output_path: Path to save the report

        Returns:
            Path to saved file
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        content = self.generate(report)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Saved markdown report to {output_path}")
        return output_path
