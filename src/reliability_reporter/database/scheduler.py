"""Scheduler for automated report generation and alerting."""

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from croniter import croniter

from ..analysis import IncidentAnalyzer
from ..categorization import CategoryGenerator, IncidentClassifier
from ..categorization.ai_client import create_ai_client
from ..config import settings
from ..fetchers import StatusPageAPIFetcher, GenericStatusPageScraper
from ..models import Incident, Report
from ..reporters import MarkdownReporter, SpreadsheetReporter
from .db import Database

logger = logging.getLogger(__name__)


class AlertChecker:
    """Check alert conditions and trigger notifications."""

    ALERT_TYPES = {
        "incident_count_daily": "Daily incident count",
        "incident_count_weekly": "Weekly incident count",
        "critical_incident": "Critical incident detected",
        "mttr_threshold": "MTTR threshold exceeded",
        "downtime_daily": "Daily downtime threshold",
    }

    def __init__(self, db: Database, notifier: Optional["NotificationManager"] = None):
        """Initialize alert checker."""
        self.db = db
        self.notifier = notifier

    async def check_alerts(self, company_name: str, incidents: list[Incident]) -> list[dict]:
        """
        Check all alert conditions for a company.

        Returns list of triggered alerts.
        """
        alerts = self.db.get_alerts(company_name)
        triggered = []

        for alert in alerts:
            result = await self._check_alert(alert, incidents)
            if result:
                triggered.append(result)

                # Record trigger
                self.db.record_alert_trigger(
                    alert["id"],
                    result["value"],
                    result["message"]
                )

                # Send notification
                if self.notifier and alert["notification_channels"]:
                    await self.notifier.send(
                        channels=alert["notification_channels"],
                        subject=f"Alert: {alert['alert_type']} for {company_name}",
                        message=result["message"],
                    )

        return triggered

    async def _check_alert(self, alert: dict, incidents: list[Incident]) -> dict | None:
        """Check a single alert condition."""
        alert_type = alert["alert_type"]
        threshold = alert["threshold_value"]
        comparison = alert["comparison"]

        value = None
        message = ""

        if alert_type == "incident_count_daily":
            today = datetime.now().date()
            daily_incidents = [
                i for i in incidents
                if (i.created_at.date() == today)
            ]
            value = len(daily_incidents)
            message = f"Daily incident count: {value}"

        elif alert_type == "incident_count_weekly":
            week_ago = datetime.now() - timedelta(days=7)
            weekly_incidents = [
                i for i in incidents
                if i.created_at >= week_ago
            ]
            value = len(weekly_incidents)
            message = f"Weekly incident count: {value}"

        elif alert_type == "critical_incident":
            critical = [i for i in incidents if i.impact == "critical" and not i.is_resolved]
            if critical:
                value = len(critical)
                message = f"Active critical incidents: {value}"

        elif alert_type == "mttr_threshold":
            analyzer = IncidentAnalyzer()
            stats = analyzer.calculate_stats(incidents)
            if stats.mttr_hours:
                value = stats.mttr_hours
                message = f"Current MTTR: {value:.1f} hours"

        elif alert_type == "downtime_daily":
            today = datetime.now().date()
            daily_downtime = sum(
                i.duration_hours or 0
                for i in incidents
                if i.created_at.date() == today
            )
            value = daily_downtime
            message = f"Daily downtime: {value:.1f} hours"

        if value is None:
            return None

        # Check threshold
        triggered = False
        if comparison == "gt" and value > threshold:
            triggered = True
        elif comparison == "lt" and value < threshold:
            triggered = True
        elif comparison == "eq" and value == threshold:
            triggered = True
        elif comparison == "gte" and value >= threshold:
            triggered = True
        elif comparison == "lte" and value <= threshold:
            triggered = True

        if triggered:
            return {
                "alert_id": alert["id"],
                "alert_type": alert_type,
                "value": value,
                "threshold": threshold,
                "message": message,
            }

        return None


class ReportScheduler:
    """Scheduler for automated report generation."""

    def __init__(
        self,
        db: Database,
        output_dir: Path = Path("./reports"),
        notifier: Optional["NotificationManager"] = None,
    ):
        """Initialize the scheduler."""
        self.db = db
        self.output_dir = output_dir
        self.notifier = notifier
        self.alert_checker = AlertChecker(db, notifier)
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_scheduler())
        logger.info("Report scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Report scheduler stopped")

    async def _run_scheduler(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                await self._check_scheduled_reports()
                await asyncio.sleep(60)  # Check every minute
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                await asyncio.sleep(60)

    async def _check_scheduled_reports(self) -> None:
        """Check and run due scheduled reports."""
        reports = self.db.get_scheduled_reports()
        now = datetime.now()

        for report in reports:
            next_run = report.get("next_run")
            if next_run:
                next_run = datetime.fromisoformat(next_run)
                if next_run > now:
                    continue

            # Calculate next run time
            cron = croniter(report["schedule"], now)
            next_run_time = cron.get_next(datetime)

            try:
                await self._generate_scheduled_report(report)
                self.db.update_scheduled_report_run(report["id"], next_run_time)
            except Exception as e:
                logger.error(f"Error generating scheduled report {report['name']}: {e}")

    async def _generate_scheduled_report(self, report_config: dict) -> None:
        """Generate a scheduled report."""
        logger.info(f"Generating scheduled report: {report_config['name']}")

        company_name = report_config["company_name"]
        company_url = report_config["company_url"]
        config = report_config.get("config", {})

        # Determine date range
        days_back = config.get("days_back", 30)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)

        # Fetch incidents
        api_fetcher = StatusPageAPIFetcher()
        generic_scraper = GenericStatusPageScraper()

        try:
            incidents = await api_fetcher.fetch_incidents(
                company_url, company_name, start_date, end_date
            )
            if not incidents:
                incidents = await generic_scraper.fetch_incidents(
                    company_url, company_name, start_date, end_date
                )

            # Store incidents in database
            self.db.add_incidents(incidents)

            # Check alerts
            await self.alert_checker.check_alerts(company_name, incidents)

            # Generate report
            category_gen = CategoryGenerator(ai_client=None)  # type: ignore
            categories = category_gen.get_default_categories()

            analyzer = IncidentAnalyzer()
            stats = analyzer.calculate_stats(incidents)
            trends = analyzer.calculate_trends(incidents, start_date, end_date)
            key_issues = analyzer._identify_key_issues_heuristic(incidents, categories)

            report = Report(
                company_name=company_name,
                peer_companies=[],
                start_date=start_date,
                end_date=end_date,
                incidents=incidents,
                categories=categories,
                stats=stats,
                trends=trends,
                key_issues=key_issues,
            )

            # Save report
            self.output_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y%m%d")
            base_name = f"{company_name.lower().replace(' ', '_')}_scheduled_{date_str}"

            md_reporter = MarkdownReporter()
            md_path = md_reporter.save(report, self.output_dir / f"{base_name}.md")

            ss_reporter = SpreadsheetReporter()
            ss_reporter.save_all(report, self.output_dir, base_name)

            logger.info(f"Scheduled report generated: {md_path}")

            # Send notification if configured
            if self.notifier and config.get("notification_channels"):
                await self.notifier.send(
                    channels=config["notification_channels"],
                    subject=f"Reliability Report: {company_name}",
                    message=f"Your scheduled reliability report is ready.\n\nSummary:\n- Total incidents: {stats.total_count}\n- Critical: {stats.critical_count}\n- MTTR: {stats.mttr_hours:.1f}h" if stats.mttr_hours else f"Your scheduled reliability report is ready.\n\nSummary:\n- Total incidents: {stats.total_count}\n- Critical: {stats.critical_count}",
                    attachments=[str(md_path)],
                )

        finally:
            await api_fetcher.close()
            await generic_scraper.close()

    async def run_report_now(self, report_id: int) -> None:
        """Manually trigger a scheduled report."""
        reports = self.db.get_scheduled_reports()
        report = next((r for r in reports if r["id"] == report_id), None)

        if not report:
            raise ValueError(f"Scheduled report not found: {report_id}")

        await self._generate_scheduled_report(report)


class NotificationManager:
    """Manage notifications via various channels."""

    def __init__(
        self,
        slack_webhook_url: str | None = None,
        email_config: dict | None = None,
    ):
        """Initialize notification manager."""
        self.slack_webhook_url = slack_webhook_url
        self.email_config = email_config

    async def send(
        self,
        channels: list[str],
        subject: str,
        message: str,
        attachments: list[str] | None = None,
    ) -> None:
        """Send notification to specified channels."""
        for channel in channels:
            try:
                if channel == "slack" and self.slack_webhook_url:
                    await self._send_slack(subject, message)
                elif channel == "email" and self.email_config:
                    await self._send_email(subject, message, attachments)
                else:
                    logger.warning(f"Unknown or unconfigured channel: {channel}")
            except Exception as e:
                logger.error(f"Error sending notification to {channel}: {e}")

    async def _send_slack(self, subject: str, message: str) -> None:
        """Send Slack notification."""
        import httpx

        payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": subject}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": message}
                }
            ]
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.slack_webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()

        logger.info(f"Slack notification sent: {subject}")

    async def _send_email(
        self,
        subject: str,
        message: str,
        attachments: list[str] | None = None,
    ) -> None:
        """Send email notification."""
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders

        if not self.email_config:
            return

        smtp_server = self.email_config.get("smtp_server")
        smtp_port = self.email_config.get("smtp_port", 587)
        username = self.email_config.get("username")
        password = self.email_config.get("password")
        from_addr = self.email_config.get("from_address")
        to_addrs = self.email_config.get("to_addresses", [])

        if not all([smtp_server, username, password, from_addr, to_addrs]):
            logger.warning("Incomplete email configuration")
            return

        msg = MIMEMultipart()
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs)
        msg["Subject"] = subject

        msg.attach(MIMEText(message, "plain"))

        # Add attachments
        if attachments:
            for filepath in attachments:
                path = Path(filepath)
                if path.exists():
                    with open(path, "rb") as f:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(f.read())
                        encoders.encode_base64(part)
                        part.add_header(
                            "Content-Disposition",
                            f"attachment; filename={path.name}",
                        )
                        msg.attach(part)

        # Send email
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(username, password)
            server.sendmail(from_addr, to_addrs, msg.as_string())

        logger.info(f"Email notification sent: {subject}")
