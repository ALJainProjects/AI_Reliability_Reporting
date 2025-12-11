#!/usr/bin/env python3
"""
Basic usage example for the reliability report generator.

This script demonstrates how to use the library programmatically.
"""

import asyncio
from datetime import datetime
from pathlib import Path

from reliability_reporter.analysis import IncidentAnalyzer
from reliability_reporter.categorization import CategoryGenerator, IncidentClassifier
from reliability_reporter.categorization.ai_client import create_ai_client
from reliability_reporter.fetchers import StatusPageAPIFetcher
from reliability_reporter.models import Report
from reliability_reporter.reporters import MarkdownReporter, SpreadsheetReporter


async def main():
    """Generate a reliability report programmatically."""

    # Configuration
    target_company = "New Relic"
    target_url = "https://status.newrelic.com"
    start_date = datetime(2024, 1, 1)
    end_date = datetime(2024, 12, 1)
    output_dir = Path("./reports")

    # Initialize fetcher
    fetcher = StatusPageAPIFetcher()

    try:
        # Step 1: Fetch incidents
        print(f"Fetching incidents from {target_company}...")
        incidents = await fetcher.fetch_incidents(
            target_url, target_company, start_date, end_date
        )
        print(f"Found {len(incidents)} incidents")

        if not incidents:
            print("No incidents found. Exiting.")
            return

        # Step 2: Generate categories (using default for this example)
        # For AI-powered categories, uncomment:
        # ai_client = create_ai_client("openai", "your-api-key")
        # category_gen = CategoryGenerator(ai_client)
        # categories = await category_gen.generate_categories(incidents)

        category_gen = CategoryGenerator(ai_client=None)  # type: ignore
        categories = category_gen.get_default_categories()
        print(f"Using {len(categories)} categories")

        # Step 3: Classify incidents (simple keyword matching)
        for incident in incidents:
            text = incident.name.lower()
            for category in categories:
                if any(kw.lower() in text for kw in category.keywords):
                    incident.category = category.id
                    break
            if not incident.category:
                incident.category = "other"

        # Step 4: Analyze
        analyzer = IncidentAnalyzer()
        stats = analyzer.calculate_stats(incidents)
        trends = analyzer.calculate_trends(incidents, start_date, end_date)
        key_issues = analyzer._identify_key_issues_heuristic(incidents, categories)

        print(f"\nStatistics:")
        print(f"  Total incidents: {stats.total_count}")
        print(f"  Critical: {stats.critical_count}")
        print(f"  MTTR: {stats.mttr_hours:.1f}h" if stats.mttr_hours else "  MTTR: N/A")

        # Step 5: Create report
        report = Report(
            company_name=target_company,
            peer_companies=[],
            start_date=start_date,
            end_date=end_date,
            incidents=incidents,
            categories=categories,
            stats=stats,
            trends=trends,
            key_issues=key_issues,
        )

        # Step 6: Generate outputs
        output_dir.mkdir(parents=True, exist_ok=True)

        md_reporter = MarkdownReporter()
        md_path = md_reporter.save(report, output_dir / "report.md")
        print(f"\nSaved markdown report: {md_path}")

        ss_reporter = SpreadsheetReporter()
        csv_path, xlsx_path = ss_reporter.save_all(report, output_dir)
        print(f"Saved CSV: {csv_path}")
        print(f"Saved Excel: {xlsx_path}")

    finally:
        await fetcher.close()


if __name__ == "__main__":
    asyncio.run(main())
