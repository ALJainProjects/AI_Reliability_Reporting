"""CLI interface for the reliability report generator."""

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn

from .analysis import IncidentAnalyzer
from .categorization import CategoryGenerator, IncidentClassifier
from .categorization.ai_client import create_ai_client
from .config import settings
from .fetchers import StatusPageAPIFetcher, StatusPageHTMLScraper
from .models import Category, CompanyConfig, Incident, Report
from .reporters import MarkdownReporter, SpreadsheetReporter, PDFReporter

console = Console()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True, console=console)],
)
logger = logging.getLogger("reliability_reporter")


def setup_logging(verbose: bool) -> None:
    """Configure logging based on verbosity."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.getLogger("reliability_reporter").setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """AI-powered reliability report generator for enterprise status pages."""
    pass


@cli.command()
@click.option(
    "--company", "-c", required=True, help="Target company name"
)
@click.option(
    "--url", "-u", required=True, help="Target company status page URL"
)
@click.option(
    "--peers",
    "-p",
    type=click.Path(exists=True),
    help="JSON file with peer companies [{name, url}, ...]",
)
@click.option(
    "--start-date",
    "-s",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    required=True,
    help="Start date (YYYY-MM-DD)",
)
@click.option(
    "--end-date",
    "-e",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=datetime.now().strftime("%Y-%m-%d"),
    help="End date (YYYY-MM-DD), defaults to today",
)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(),
    default="./reports",
    help="Output directory for reports",
)
@click.option(
    "--provider",
    type=click.Choice(["openai", "anthropic"]),
    default=None,
    help="AI provider (default: from env or openai)",
)
@click.option(
    "--api-key",
    envvar=["OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
    help="AI provider API key (or set via environment)",
)
@click.option(
    "--skip-ai",
    is_flag=True,
    help="Skip AI categorization (use default categories)",
)
@click.option(
    "--pdf", is_flag=True, help="Generate PDF report in addition to other formats"
)
@click.option(
    "--verbose", "-v", is_flag=True, help="Enable verbose output"
)
def generate(
    company: str,
    url: str,
    peers: Optional[str],
    start_date: datetime,
    end_date: datetime,
    output_dir: str,
    provider: Optional[str],
    api_key: Optional[str],
    skip_ai: bool,
    pdf: bool,
    verbose: bool,
):
    """Generate a reliability report for a company."""
    setup_logging(verbose)

    # Parse peer companies
    peer_configs = []
    if peers:
        with open(peers) as f:
            peer_data = json.load(f)
            for p in peer_data:
                peer_configs.append(
                    CompanyConfig(name=p["name"], url=p["url"], is_target=False)
                )

    # Create target config
    target_config = CompanyConfig(name=company, url=url, is_target=True)

    # Determine AI provider
    ai_provider = provider or settings.ai_provider

    # Get API key
    if not skip_ai:
        if api_key:
            actual_key = api_key
        else:
            try:
                actual_key = settings.get_api_key(ai_provider)
            except ValueError as e:
                console.print(f"[red]Error: {e}[/red]")
                console.print(
                    f"[yellow]Set {ai_provider.upper()}_API_KEY environment variable or use --api-key[/yellow]"
                )
                console.print("[yellow]Or use --skip-ai to use default categories[/yellow]")
                sys.exit(1)
    else:
        actual_key = None

    # Run async generation
    try:
        asyncio.run(
            _generate_report(
                target_config=target_config,
                peer_configs=peer_configs,
                start_date=start_date,
                end_date=end_date,
                output_dir=Path(output_dir),
                ai_provider=ai_provider,
                api_key=actual_key,
                skip_ai=skip_ai,
                generate_pdf=pdf,
            )
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if verbose:
            console.print_exception()
        sys.exit(1)


async def _generate_report(
    target_config: CompanyConfig,
    peer_configs: list[CompanyConfig],
    start_date: datetime,
    end_date: datetime,
    output_dir: Path,
    ai_provider: str,
    api_key: Optional[str],
    skip_ai: bool,
    generate_pdf: bool = False,
):
    """Async implementation of report generation."""
    api_fetcher = StatusPageAPIFetcher()
    html_scraper = StatusPageHTMLScraper()

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            # Step 1: Fetch incidents from all companies
            all_incidents: list[Incident] = []
            peer_incidents_map: dict[str, list[Incident]] = {}

            # Fetch target company
            task = progress.add_task(
                f"Fetching incidents from {target_config.name}...", total=None
            )
            target_incidents = await _fetch_company_incidents(
                api_fetcher, html_scraper, target_config, start_date, end_date
            )
            all_incidents.extend(target_incidents)
            progress.update(task, completed=True)
            console.print(
                f"  [green]Found {len(target_incidents)} incidents from {target_config.name}[/green]"
            )

            # Fetch peer companies
            for peer_config in peer_configs:
                task = progress.add_task(
                    f"Fetching incidents from {peer_config.name}...", total=None
                )
                peer_incidents = await _fetch_company_incidents(
                    api_fetcher, html_scraper, peer_config, start_date, end_date
                )
                all_incidents.extend(peer_incidents)
                peer_incidents_map[peer_config.name] = peer_incidents
                progress.update(task, completed=True)
                console.print(
                    f"  [green]Found {len(peer_incidents)} incidents from {peer_config.name}[/green]"
                )

            if not target_incidents:
                console.print(
                    f"[yellow]Warning: No incidents found for {target_config.name}[/yellow]"
                )

            # Step 2: Generate or use default categories
            if skip_ai or not api_key:
                task = progress.add_task("Using default categories...", total=None)
                category_gen = CategoryGenerator(ai_client=None)  # type: ignore
                categories = category_gen.get_default_categories()
                progress.update(task, completed=True)
            else:
                task = progress.add_task(
                    "Generating categories from all incidents...", total=None
                )
                ai_client = create_ai_client(ai_provider, api_key)
                try:
                    category_gen = CategoryGenerator(ai_client)
                    categories = await category_gen.generate_categories(all_incidents)
                finally:
                    await ai_client.close()
                progress.update(task, completed=True)

            console.print(f"  [green]Generated {len(categories)} categories[/green]")

            # Step 3: Classify incidents
            if skip_ai or not api_key:
                task = progress.add_task(
                    "Classifying incidents (heuristic)...", total=None
                )
                # Simple keyword-based classification
                _classify_incidents_heuristic(all_incidents, categories)
                progress.update(task, completed=True)
            else:
                task = progress.add_task(
                    "Classifying incidents with AI...", total=None
                )
                ai_client = create_ai_client(ai_provider, api_key)
                try:
                    classifier = IncidentClassifier(ai_client, categories)
                    all_incidents = await classifier.classify_all(all_incidents)
                    # Update category incident counts
                    for cat in categories:
                        cat.incident_count = sum(
                            1 for i in all_incidents if i.category == cat.id
                        )
                finally:
                    await ai_client.close()
                progress.update(task, completed=True)

            console.print(
                f"  [green]Classified {len(all_incidents)} incidents[/green]"
            )

            # Step 4: Analyze and generate report
            task = progress.add_task("Analyzing trends and statistics...", total=None)

            # Create AI client for key issues if available
            ai_client_for_analysis = None
            if not skip_ai and api_key:
                ai_client_for_analysis = create_ai_client(ai_provider, api_key)

            try:
                analyzer = IncidentAnalyzer(ai_client_for_analysis)

                # Filter to just target incidents for the report
                target_only = [
                    i for i in all_incidents if i.company_name == target_config.name
                ]

                stats = analyzer.calculate_stats(target_only)
                trends = analyzer.calculate_trends(target_only, start_date, end_date)

                # Generate key issues
                if ai_client_for_analysis:
                    key_issues = await analyzer.identify_key_issues(
                        target_only, target_config.name, start_date, end_date, categories
                    )
                else:
                    key_issues = analyzer._identify_key_issues_heuristic(
                        target_only, categories
                    )

                # Compare with peers
                peer_comparisons = analyzer.compare_with_peers(
                    target_only, peer_incidents_map
                )

            finally:
                if ai_client_for_analysis:
                    await ai_client_for_analysis.close()

            progress.update(task, completed=True)

            # Create report object
            report = Report(
                company_name=target_config.name,
                peer_companies=[p.name for p in peer_configs],
                start_date=start_date,
                end_date=end_date,
                incidents=target_only,
                categories=categories,
                stats=stats,
                trends=trends,
                key_issues=key_issues,
                peer_comparisons=peer_comparisons,
            )

            # Step 5: Generate outputs
            task = progress.add_task("Generating reports...", total=None)

            output_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y%m%d")
            base_name = f"{target_config.name.lower().replace(' ', '_')}_reliability_{date_str}"

            # Markdown report
            md_reporter = MarkdownReporter()
            md_path = md_reporter.save(report, output_dir / f"{base_name}.md")

            # Spreadsheet reports
            ss_reporter = SpreadsheetReporter()
            csv_path, xlsx_path = ss_reporter.save_all(report, output_dir, base_name)

            # PDF report (optional)
            pdf_path = None
            if generate_pdf:
                pdf_reporter = PDFReporter()
                pdf_path = pdf_reporter.generate(report, output_dir / f"{base_name}.pdf")

            # Save categories JSON
            categories_path = output_dir / f"{base_name}_categories.json"
            with open(categories_path, "w") as f:
                json.dump(
                    [
                        {
                            "id": c.id,
                            "name": c.name,
                            "description": c.description,
                            "keywords": c.keywords,
                            "incident_count": c.incident_count,
                        }
                        for c in categories
                    ],
                    f,
                    indent=2,
                )

            progress.update(task, completed=True)

        # Print summary
        console.print("\n[bold green]Report generation complete![/bold green]\n")
        console.print(f"[bold]Output files:[/bold]")
        console.print(f"  - Markdown report: {md_path}")
        console.print(f"  - CSV spreadsheet: {csv_path}")
        console.print(f"  - Excel spreadsheet: {xlsx_path}")
        if pdf_path:
            console.print(f"  - PDF report: {pdf_path}")
        console.print(f"  - Categories JSON: {categories_path}")
        console.print(f"\n[bold]Summary:[/bold]")
        console.print(f"  - Total incidents analyzed: {len(target_only)}")
        console.print(f"  - Categories: {len(categories)}")
        console.print(f"  - MTTR: {stats.mttr_hours:.1f} hours" if stats.mttr_hours else "  - MTTR: N/A")
        console.print(f"  - Critical incidents: {stats.critical_count}")

    finally:
        await api_fetcher.close()
        await html_scraper.close()


def _make_naive(dt: datetime) -> datetime:
    """Make a datetime naive by removing timezone info."""
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


async def _fetch_company_incidents(
    api_fetcher: StatusPageAPIFetcher,
    html_scraper: StatusPageHTMLScraper,
    config: CompanyConfig,
    start_date: datetime,
    end_date: datetime,
) -> list[Incident]:
    """Fetch incidents for a company, with fallback to HTML scraping."""
    # Try API first
    incidents = await api_fetcher.fetch_incidents(
        config.url, config.name, start_date, end_date
    )

    # Check if we need more historical data
    if incidents:
        oldest = min(_make_naive(i.created_at) for i in incidents)
        start_naive = _make_naive(start_date)
        if oldest > start_naive:
            logger.debug(
                f"API only returned data from {oldest}, trying HTML scraper for older data"
            )
            # Try HTML scraper for older data
            html_incidents = await html_scraper.fetch_incidents(
                config.url, config.name, start_date, oldest
            )
            incidents.extend(html_incidents)
    elif not incidents:
        # API returned nothing, try HTML
        logger.debug(f"No API data, trying HTML scraper for {config.name}")
        incidents = await html_scraper.fetch_incidents(
            config.url, config.name, start_date, end_date
        )

    # Deduplicate
    seen_ids = set()
    unique = []
    for incident in incidents:
        if incident.id not in seen_ids:
            seen_ids.add(incident.id)
            unique.append(incident)

    return unique


def _classify_incidents_heuristic(
    incidents: list[Incident], categories: list[Category]
) -> None:
    """Simple keyword-based classification when AI is not available."""
    for incident in incidents:
        best_match = "other"
        best_score = 0

        text = (incident.name + " " + incident.get_full_description()).lower()

        for category in categories:
            if category.id == "other":
                continue

            score = 0
            for keyword in category.keywords:
                if keyword.lower() in text:
                    score += 1

            if score > best_score:
                best_score = score
                best_match = category.id

        incident.category = best_match
        incident.category_confidence = min(best_score / 3, 1.0) if best_score > 0 else 0.0


@cli.command()
@click.option("--url", "-u", required=True, help="Status page URL to test")
def test_fetch(url: str):
    """Test fetching incidents from a status page."""
    async def _test():
        fetcher = StatusPageAPIFetcher()
        try:
            # Check API availability
            console.print(f"Testing: {url}")
            available = await fetcher.check_api_available(url)
            console.print(f"API available: {available}")

            if available:
                info = await fetcher.fetch_status_page_info(url)
                console.print(f"Company: {info.company_name}")
                console.print(f"Components: {len(info.components)}")

                incidents = await fetcher.fetch_incidents(url, info.company_name)
                console.print(f"Recent incidents: {len(incidents)}")

                for incident in incidents[:5]:
                    console.print(
                        f"  - [{incident.impact}] {incident.name[:60]}..."
                    )
        finally:
            await fetcher.close()

    asyncio.run(_test())


@cli.command()
def list_providers():
    """List available AI providers and their status."""
    console.print("[bold]AI Providers:[/bold]\n")

    # OpenAI
    openai_status = "configured" if settings.openai_api_key else "[red]not configured[/red]"
    console.print(f"  openai: {openai_status}")
    console.print(f"    Model: {settings.openai_model}")

    # Anthropic
    anthropic_status = (
        "configured" if settings.anthropic_api_key else "[red]not configured[/red]"
    )
    console.print(f"  anthropic: {anthropic_status}")
    console.print(f"    Model: {settings.anthropic_model}")

    console.print(f"\n[bold]Default provider:[/bold] {settings.ai_provider}")


@cli.command()
@click.option(
    "--host", "-h", default="127.0.0.1", help="Host to bind to"
)
@click.option(
    "--port", "-p", default=8000, type=int, help="Port to bind to"
)
@click.option(
    "--reload", is_flag=True, help="Enable auto-reload for development"
)
def serve(host: str, port: int, reload: bool):
    """Start the web UI server."""
    import uvicorn
    console.print(f"[bold]Starting web server at http://{host}:{port}[/bold]")
    uvicorn.run(
        "reliability_reporter.web.app:app",
        host=host,
        port=port,
        reload=reload,
    )


@cli.command()
@click.option(
    "--db-path", "-d", type=click.Path(), default="./reliability.db",
    help="Path to SQLite database"
)
@click.option(
    "--output-dir", "-o", type=click.Path(), default="./reports",
    help="Directory for generated reports"
)
def scheduler(db_path: str, output_dir: str):
    """Start the report scheduler daemon."""
    from .database import Database, ReportScheduler

    console.print("[bold]Starting report scheduler...[/bold]")
    console.print(f"  Database: {db_path}")
    console.print(f"  Output dir: {output_dir}")

    db = Database(db_path)
    scheduler_instance = ReportScheduler(db, Path(output_dir))

    try:
        asyncio.run(_run_scheduler(scheduler_instance))
    except KeyboardInterrupt:
        console.print("\n[yellow]Scheduler stopped[/yellow]")


async def _run_scheduler(scheduler_instance):
    """Run the scheduler until interrupted."""
    await scheduler_instance.start()
    try:
        while True:
            await asyncio.sleep(60)
    finally:
        await scheduler_instance.stop()


@cli.command()
@click.option(
    "--db-path", "-d", type=click.Path(), default="./reliability.db",
    help="Path to SQLite database"
)
def init_db(db_path: str):
    """Initialize the database."""
    from .database import Database

    console.print(f"[bold]Initializing database at {db_path}...[/bold]")
    db = Database(db_path)
    console.print("[green]Database initialized successfully![/green]")


if __name__ == "__main__":
    cli()
