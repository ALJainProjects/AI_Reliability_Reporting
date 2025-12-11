"""FastAPI web application for the reliability report generator."""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from ..analysis import IncidentAnalyzer
from ..categorization import CategoryGenerator, IncidentClassifier
from ..categorization.ai_client import create_ai_client
from ..config import settings
from ..fetchers import StatusPageAPIFetcher, StatusPageHTMLScraper, GenericStatusPageScraper
from ..models import CompanyConfig, Incident, Report
from ..reporters import MarkdownReporter, SpreadsheetReporter, PDFReporter

logger = logging.getLogger(__name__)

# Store for background job status
job_store: dict[str, dict] = {}

# Base paths
BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
REPORTS_DIR = Path("./reports")


class GenerateReportRequest(BaseModel):
    """Request model for report generation."""
    company_name: str
    company_url: str
    peers: list[dict] = []  # [{"name": "...", "url": "..."}]
    start_date: str  # YYYY-MM-DD
    end_date: str | None = None
    provider: str = "openai"
    skip_ai: bool = False
    generate_pdf: bool = False


class JobStatus(BaseModel):
    """Job status response."""
    job_id: str
    status: str  # pending, running, completed, failed
    progress: int  # 0-100
    message: str
    result: dict | None = None


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Reliability Report Generator",
        description="AI-powered reliability report generator for enterprise status pages",
        version="0.1.0",
    )

    # Mount static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Setup templates
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        """Render the home page."""
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "title": "Reliability Report Generator",
            },
        )

    @app.get("/reports", response_class=HTMLResponse)
    async def reports_page(request: Request):
        """List all generated reports."""
        reports = []
        if REPORTS_DIR.exists():
            for md_file in REPORTS_DIR.glob("*.md"):
                files = {
                    "markdown": md_file.name,
                    "csv": md_file.stem + ".csv",
                    "xlsx": md_file.stem + ".xlsx",
                }
                # Check if PDF exists
                pdf_file = REPORTS_DIR / (md_file.stem + ".pdf")
                if pdf_file.exists():
                    files["pdf"] = md_file.stem + ".pdf"

                reports.append({
                    "name": md_file.stem,
                    "date": datetime.fromtimestamp(md_file.stat().st_mtime),
                    "files": files,
                })
        reports.sort(key=lambda x: x["date"], reverse=True)

        return templates.TemplateResponse(
            "reports.html",
            {"request": request, "reports": reports},
        )

    @app.get("/report/{report_name}", response_class=HTMLResponse)
    async def view_report(request: Request, report_name: str):
        """View a specific report."""
        md_path = REPORTS_DIR / f"{report_name}.md"
        if not md_path.exists():
            raise HTTPException(status_code=404, detail="Report not found")

        # Read markdown content
        content = md_path.read_text()

        # Try to load incident data
        json_path = REPORTS_DIR / f"{report_name}_categories.json"
        categories = []
        if json_path.exists():
            categories = json.loads(json_path.read_text())

        return templates.TemplateResponse(
            "view_report.html",
            {
                "request": request,
                "report_name": report_name,
                "content": content,
                "categories": categories,
            },
        )

    @app.get("/download/{report_name}/{file_type}")
    async def download_report(report_name: str, file_type: str):
        """Download a report file."""
        extensions = {
            "markdown": ".md",
            "csv": ".csv",
            "xlsx": ".xlsx",
            "json": "_categories.json",
            "pdf": ".pdf",
        }
        ext = extensions.get(file_type)
        if not ext:
            raise HTTPException(status_code=400, detail="Invalid file type")

        file_path = REPORTS_DIR / f"{report_name}{ext}"
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")

        media_types = {
            ".md": "text/markdown",
            ".csv": "text/csv",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "_categories.json": "application/json",
            ".pdf": "application/pdf",
        }

        return FileResponse(
            path=str(file_path),
            filename=file_path.name,
            media_type=media_types.get(ext, "application/octet-stream"),
        )

    @app.post("/api/generate")
    async def generate_report(
        request: GenerateReportRequest,
        background_tasks: BackgroundTasks,
    ):
        """Start report generation as a background task."""
        job_id = str(uuid4())

        job_store[job_id] = {
            "status": "pending",
            "progress": 0,
            "message": "Starting report generation...",
            "result": None,
        }

        background_tasks.add_task(
            _generate_report_task,
            job_id,
            request,
        )

        return {"job_id": job_id}

    @app.get("/api/status/{job_id}")
    async def get_job_status(job_id: str) -> JobStatus:
        """Get the status of a background job."""
        if job_id not in job_store:
            raise HTTPException(status_code=404, detail="Job not found")

        job = job_store[job_id]
        return JobStatus(
            job_id=job_id,
            status=job["status"],
            progress=job["progress"],
            message=job["message"],
            result=job.get("result"),
        )

    @app.get("/api/test-url")
    async def test_url(url: str):
        """Test if a status page URL is accessible."""
        fetcher = StatusPageAPIFetcher()
        try:
            available = await fetcher.check_api_available(url)
            info = await fetcher.fetch_status_page_info(url)
            return {
                "success": True,
                "api_available": available,
                "company_name": info.company_name,
                "components": len(info.components),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            await fetcher.close()

    @app.get("/api/providers")
    async def list_providers():
        """List configured AI providers."""
        return {
            "providers": [
                {
                    "id": "openai",
                    "name": "OpenAI",
                    "configured": bool(settings.openai_api_key),
                    "model": settings.openai_model,
                },
                {
                    "id": "anthropic",
                    "name": "Anthropic",
                    "configured": bool(settings.anthropic_api_key),
                    "model": settings.anthropic_model,
                },
            ],
            "default": settings.ai_provider,
        }

    return app


async def _generate_report_task(job_id: str, request: GenerateReportRequest):
    """Background task to generate a report."""
    try:
        job_store[job_id]["status"] = "running"
        job_store[job_id]["message"] = "Initializing..."

        # Parse dates
        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        end_date = (
            datetime.strptime(request.end_date, "%Y-%m-%d")
            if request.end_date
            else datetime.now()
        )

        # Initialize fetchers
        api_fetcher = StatusPageAPIFetcher()
        html_scraper = StatusPageHTMLScraper()
        generic_scraper = GenericStatusPageScraper()

        try:
            # Fetch target incidents
            job_store[job_id]["progress"] = 10
            job_store[job_id]["message"] = f"Fetching incidents from {request.company_name}..."

            # Try API first, then generic scraper
            target_incidents = await api_fetcher.fetch_incidents(
                request.company_url, request.company_name, start_date, end_date
            )

            if not target_incidents:
                target_incidents = await generic_scraper.fetch_incidents(
                    request.company_url, request.company_name, start_date, end_date
                )

            all_incidents = list(target_incidents)
            peer_incidents_map: dict[str, list[Incident]] = {}

            # Fetch peer incidents
            job_store[job_id]["progress"] = 20
            for i, peer in enumerate(request.peers):
                job_store[job_id]["message"] = f"Fetching incidents from {peer['name']}..."
                peer_incidents = await api_fetcher.fetch_incidents(
                    peer["url"], peer["name"], start_date, end_date
                )
                if not peer_incidents:
                    peer_incidents = await generic_scraper.fetch_incidents(
                        peer["url"], peer["name"], start_date, end_date
                    )
                all_incidents.extend(peer_incidents)
                peer_incidents_map[peer["name"]] = peer_incidents
                job_store[job_id]["progress"] = 20 + int(30 * (i + 1) / max(len(request.peers), 1))

            # Generate categories
            job_store[job_id]["progress"] = 50
            job_store[job_id]["message"] = "Generating categories..."

            if request.skip_ai or not _get_api_key(request.provider):
                category_gen = CategoryGenerator(ai_client=None)  # type: ignore
                categories = category_gen.get_default_categories()
            else:
                api_key = _get_api_key(request.provider)
                ai_client = create_ai_client(request.provider, api_key)
                try:
                    category_gen = CategoryGenerator(ai_client)
                    categories = await category_gen.generate_categories(all_incidents)
                finally:
                    await ai_client.close()

            # Classify incidents
            job_store[job_id]["progress"] = 60
            job_store[job_id]["message"] = "Classifying incidents..."

            if request.skip_ai or not _get_api_key(request.provider):
                _classify_heuristic(all_incidents, categories)
            else:
                api_key = _get_api_key(request.provider)
                ai_client = create_ai_client(request.provider, api_key)
                try:
                    classifier = IncidentClassifier(ai_client, categories)
                    all_incidents = await classifier.classify_all(all_incidents)
                finally:
                    await ai_client.close()

            # Analyze
            job_store[job_id]["progress"] = 80
            job_store[job_id]["message"] = "Analyzing trends..."

            target_only = [i for i in all_incidents if i.company_name == request.company_name]

            analyzer = IncidentAnalyzer()
            stats = analyzer.calculate_stats(target_only)
            trends = analyzer.calculate_trends(target_only, start_date, end_date)
            key_issues = analyzer._identify_key_issues_heuristic(target_only, categories)
            peer_comparisons = analyzer.compare_with_peers(target_only, peer_incidents_map)

            # Create report
            report = Report(
                company_name=request.company_name,
                peer_companies=[p["name"] for p in request.peers],
                start_date=start_date,
                end_date=end_date,
                incidents=target_only,
                categories=categories,
                stats=stats,
                trends=trends,
                key_issues=key_issues,
                peer_comparisons=peer_comparisons,
            )

            # Generate outputs
            job_store[job_id]["progress"] = 90
            job_store[job_id]["message"] = "Generating reports..."

            REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = f"{request.company_name.lower().replace(' ', '_')}_{date_str}"

            md_reporter = MarkdownReporter()
            md_path = md_reporter.save(report, REPORTS_DIR / f"{base_name}.md")

            ss_reporter = SpreadsheetReporter()
            csv_path, xlsx_path = ss_reporter.save_all(report, REPORTS_DIR, base_name)

            # PDF report (optional)
            pdf_path = None
            if request.generate_pdf:
                try:
                    pdf_reporter = PDFReporter()
                    pdf_path = pdf_reporter.generate(report, REPORTS_DIR / f"{base_name}.pdf")
                except ImportError:
                    logger.warning("reportlab not installed, skipping PDF generation")

            # Save categories
            categories_path = REPORTS_DIR / f"{base_name}_categories.json"
            with open(categories_path, "w") as f:
                json.dump(
                    [{"id": c.id, "name": c.name, "description": c.description, "incident_count": c.incident_count}
                     for c in categories],
                    f,
                    indent=2,
                )

            job_store[job_id]["progress"] = 100
            job_store[job_id]["status"] = "completed"
            job_store[job_id]["message"] = "Report generated successfully!"
            result_files = {
                "markdown": str(md_path),
                "csv": str(csv_path),
                "xlsx": str(xlsx_path),
            }
            if pdf_path:
                result_files["pdf"] = str(pdf_path)

            job_store[job_id]["result"] = {
                "report_name": base_name,
                "incident_count": len(target_only),
                "category_count": len(categories),
                "mttr_hours": stats.mttr_hours,
                "files": result_files,
            }

        finally:
            await api_fetcher.close()
            await html_scraper.close()
            await generic_scraper.close()

    except Exception as e:
        logger.exception("Error generating report")
        job_store[job_id]["status"] = "failed"
        job_store[job_id]["message"] = f"Error: {str(e)}"


def _get_api_key(provider: str) -> str | None:
    """Get API key for provider."""
    if provider == "openai":
        return settings.openai_api_key
    elif provider == "anthropic":
        return settings.anthropic_api_key
    return None


def _classify_heuristic(incidents: list[Incident], categories: list) -> None:
    """Simple keyword-based classification."""
    for incident in incidents:
        best_match = "other"
        best_score = 0
        text = (incident.name + " " + incident.get_full_description()).lower()

        for category in categories:
            if category.id == "other":
                continue
            score = sum(1 for kw in category.keywords if kw.lower() in text)
            if score > best_score:
                best_score = score
                best_match = category.id

        incident.category = best_match


# Create the app instance
app = create_app()
