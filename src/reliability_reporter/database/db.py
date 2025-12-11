"""SQLite database for storing incidents and historical trends."""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from ..models import Category, Incident, IncidentStats, TrendData

logger = logging.getLogger(__name__)


class Database:
    """SQLite database for historical incident data and trends."""

    def __init__(self, db_path: str | Path = "reliability_data.db"):
        """
        Initialize the database.

        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Companies table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    url TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Incidents table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    company_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    impact TEXT NOT NULL,
                    category TEXT,
                    summary TEXT,
                    root_cause TEXT,
                    created_at TIMESTAMP NOT NULL,
                    resolved_at TIMESTAMP,
                    duration_minutes REAL,
                    raw_data TEXT,
                    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (company_id) REFERENCES companies(id)
                )
            """)

            # Categories table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    keywords TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Daily stats table (for trends)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    date DATE NOT NULL,
                    incident_count INTEGER DEFAULT 0,
                    critical_count INTEGER DEFAULT 0,
                    major_count INTEGER DEFAULT 0,
                    minor_count INTEGER DEFAULT 0,
                    avg_duration_minutes REAL,
                    total_downtime_minutes REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (company_id) REFERENCES companies(id),
                    UNIQUE(company_id, date)
                )
            """)

            # Scheduled reports table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    company_id INTEGER NOT NULL,
                    peer_company_ids TEXT,
                    schedule TEXT NOT NULL,
                    last_run TIMESTAMP,
                    next_run TIMESTAMP,
                    enabled INTEGER DEFAULT 1,
                    config TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (company_id) REFERENCES companies(id)
                )
            """)

            # Alerts table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    alert_type TEXT NOT NULL,
                    threshold_value REAL,
                    comparison TEXT,
                    notification_channels TEXT,
                    enabled INTEGER DEFAULT 1,
                    last_triggered TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (company_id) REFERENCES companies(id)
                )
            """)

            # Alert history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alert_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_id INTEGER NOT NULL,
                    triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    value REAL,
                    message TEXT,
                    notified INTEGER DEFAULT 0,
                    FOREIGN KEY (alert_id) REFERENCES alerts(id)
                )
            """)

            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_company ON incidents(company_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_category ON incidents(category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_stats_company_date ON daily_stats(company_id, date)")

            conn.commit()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # Company operations
    def add_company(self, name: str, url: str) -> int:
        """Add or update a company."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO companies (name, url, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET
                    url = excluded.url,
                    updated_at = CURRENT_TIMESTAMP
            """, (name, url))
            conn.commit()

            cursor.execute("SELECT id FROM companies WHERE name = ?", (name,))
            return cursor.fetchone()["id"]

    def get_company(self, name: str) -> dict | None:
        """Get company by name."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM companies WHERE name = ?", (name,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_companies(self) -> list[dict]:
        """Get all companies."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM companies ORDER BY name")
            return [dict(row) for row in cursor.fetchall()]

    # Incident operations
    def add_incident(self, incident: Incident) -> None:
        """Add or update an incident."""
        company = self.get_company(incident.company_name)
        if not company:
            company_id = self.add_company(incident.company_name, incident.source_url)
        else:
            company_id = company["id"]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO incidents (id, company_id, name, status, impact, category, summary, root_cause, created_at, resolved_at, duration_minutes, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    category = excluded.category,
                    summary = excluded.summary,
                    root_cause = excluded.root_cause,
                    resolved_at = excluded.resolved_at,
                    duration_minutes = excluded.duration_minutes,
                    fetched_at = CURRENT_TIMESTAMP
            """, (
                incident.id,
                company_id,
                incident.name,
                incident.status,
                incident.impact,
                incident.category,
                incident.summary,
                incident.root_cause,
                incident.created_at.isoformat(),
                incident.resolved_at.isoformat() if incident.resolved_at else None,
                incident.duration_minutes,
                incident.model_dump_json(),
            ))
            conn.commit()

    def add_incidents(self, incidents: list[Incident]) -> int:
        """Add multiple incidents."""
        count = 0
        for incident in incidents:
            try:
                self.add_incident(incident)
                count += 1
            except Exception as e:
                logger.warning(f"Error adding incident {incident.id}: {e}")
        return count

    def get_incidents(
        self,
        company_name: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        category: str | None = None,
        impact: str | None = None,
        limit: int = 1000,
    ) -> list[Incident]:
        """Get incidents with filtering."""
        company = self.get_company(company_name)
        if not company:
            return []

        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT raw_data FROM incidents WHERE company_id = ?"
            params: list = [company["id"]]

            if start_date:
                query += " AND created_at >= ?"
                params.append(start_date.isoformat())
            if end_date:
                query += " AND created_at <= ?"
                params.append(end_date.isoformat())
            if category:
                query += " AND category = ?"
                params.append(category)
            if impact:
                query += " AND impact = ?"
                params.append(impact)

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)

            incidents = []
            for row in cursor.fetchall():
                try:
                    data = json.loads(row["raw_data"])
                    incidents.append(Incident.model_validate(data))
                except Exception as e:
                    logger.warning(f"Error parsing incident: {e}")

            return incidents

    def get_incident_count(self, company_name: str) -> int:
        """Get total incident count for a company."""
        company = self.get_company(company_name)
        if not company:
            return 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as count FROM incidents WHERE company_id = ?",
                (company["id"],)
            )
            return cursor.fetchone()["count"]

    # Daily stats operations
    def update_daily_stats(self, company_name: str, date: datetime) -> None:
        """Update daily statistics for a company."""
        company = self.get_company(company_name)
        if not company:
            return

        date_str = date.strftime("%Y-%m-%d")

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Get incidents for that day
            cursor.execute("""
                SELECT impact, duration_minutes
                FROM incidents
                WHERE company_id = ? AND DATE(created_at) = ?
            """, (company["id"], date_str))

            incidents = cursor.fetchall()

            incident_count = len(incidents)
            critical_count = sum(1 for i in incidents if i["impact"] == "critical")
            major_count = sum(1 for i in incidents if i["impact"] == "major")
            minor_count = sum(1 for i in incidents if i["impact"] == "minor")

            durations = [i["duration_minutes"] for i in incidents if i["duration_minutes"]]
            avg_duration = sum(durations) / len(durations) if durations else None
            total_downtime = sum(durations) if durations else 0

            cursor.execute("""
                INSERT INTO daily_stats (company_id, date, incident_count, critical_count, major_count, minor_count, avg_duration_minutes, total_downtime_minutes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, date) DO UPDATE SET
                    incident_count = excluded.incident_count,
                    critical_count = excluded.critical_count,
                    major_count = excluded.major_count,
                    minor_count = excluded.minor_count,
                    avg_duration_minutes = excluded.avg_duration_minutes,
                    total_downtime_minutes = excluded.total_downtime_minutes
            """, (company["id"], date_str, incident_count, critical_count, major_count, minor_count, avg_duration, total_downtime))

            conn.commit()

    def get_trends(
        self,
        company_name: str,
        start_date: datetime,
        end_date: datetime,
        period: str = "day",  # day, week, month
    ) -> list[TrendData]:
        """Get trend data for a company."""
        company = self.get_company(company_name)
        if not company:
            return []

        with self._get_connection() as conn:
            cursor = conn.cursor()

            if period == "day":
                group_by = "date"
                date_format = "%Y-%m-%d"
            elif period == "week":
                group_by = "strftime('%Y-%W', date)"
                date_format = "%Y-W%W"
            else:  # month
                group_by = "strftime('%Y-%m', date)"
                date_format = "%Y-%m"

            cursor.execute(f"""
                SELECT
                    {group_by} as period,
                    SUM(incident_count) as incident_count,
                    SUM(critical_count) as critical_count,
                    SUM(major_count) as major_count,
                    AVG(avg_duration_minutes) as avg_duration_minutes,
                    SUM(total_downtime_minutes) as total_downtime_minutes
                FROM daily_stats
                WHERE company_id = ? AND date >= ? AND date <= ?
                GROUP BY {group_by}
                ORDER BY period
            """, (company["id"], start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")))

            trends = []
            for row in cursor.fetchall():
                avg_hours = row["avg_duration_minutes"] / 60 if row["avg_duration_minutes"] else None
                total_hours = row["total_downtime_minutes"] / 60 if row["total_downtime_minutes"] else 0

                trends.append(TrendData(
                    period=row["period"],
                    period_start=start_date,
                    period_end=end_date,
                    incident_count=row["incident_count"] or 0,
                    critical_count=row["critical_count"] or 0,
                    major_count=row["major_count"] or 0,
                    avg_duration_hours=avg_hours,
                    total_downtime_hours=total_hours,
                ))

            return trends

    # Category operations
    def add_category(self, category: Category) -> None:
        """Add or update a category."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO categories (id, name, description, keywords)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    keywords = excluded.keywords
            """, (
                category.id,
                category.name,
                category.description,
                json.dumps(category.keywords),
            ))
            conn.commit()

    def get_categories(self) -> list[Category]:
        """Get all categories."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM categories")

            categories = []
            for row in cursor.fetchall():
                categories.append(Category(
                    id=row["id"],
                    name=row["name"],
                    description=row["description"] or "",
                    keywords=json.loads(row["keywords"]) if row["keywords"] else [],
                ))

            return categories

    # Alert operations
    def add_alert(
        self,
        company_name: str,
        alert_type: str,
        threshold_value: float,
        comparison: str = "gt",  # gt, lt, eq
        notification_channels: list[str] | None = None,
    ) -> int:
        """Add an alert rule."""
        company = self.get_company(company_name)
        if not company:
            raise ValueError(f"Company not found: {company_name}")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO alerts (company_id, alert_type, threshold_value, comparison, notification_channels)
                VALUES (?, ?, ?, ?, ?)
            """, (
                company["id"],
                alert_type,
                threshold_value,
                comparison,
                json.dumps(notification_channels or []),
            ))
            conn.commit()
            return cursor.lastrowid

    def get_alerts(self, company_name: str | None = None) -> list[dict]:
        """Get alerts, optionally filtered by company."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            if company_name:
                company = self.get_company(company_name)
                if not company:
                    return []
                cursor.execute("""
                    SELECT a.*, c.name as company_name
                    FROM alerts a
                    JOIN companies c ON a.company_id = c.id
                    WHERE a.company_id = ? AND a.enabled = 1
                """, (company["id"],))
            else:
                cursor.execute("""
                    SELECT a.*, c.name as company_name
                    FROM alerts a
                    JOIN companies c ON a.company_id = c.id
                    WHERE a.enabled = 1
                """)

            alerts = []
            for row in cursor.fetchall():
                alert = dict(row)
                alert["notification_channels"] = json.loads(alert["notification_channels"])
                alerts.append(alert)

            return alerts

    def record_alert_trigger(self, alert_id: int, value: float, message: str) -> None:
        """Record an alert trigger."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO alert_history (alert_id, value, message)
                VALUES (?, ?, ?)
            """, (alert_id, value, message))

            cursor.execute("""
                UPDATE alerts SET last_triggered = CURRENT_TIMESTAMP WHERE id = ?
            """, (alert_id,))

            conn.commit()

    # Scheduled report operations
    def add_scheduled_report(
        self,
        name: str,
        company_name: str,
        schedule: str,  # cron expression
        peer_company_names: list[str] | None = None,
        config: dict | None = None,
    ) -> int:
        """Add a scheduled report."""
        company = self.get_company(company_name)
        if not company:
            raise ValueError(f"Company not found: {company_name}")

        peer_ids = []
        if peer_company_names:
            for peer_name in peer_company_names:
                peer = self.get_company(peer_name)
                if peer:
                    peer_ids.append(peer["id"])

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO scheduled_reports (name, company_id, peer_company_ids, schedule, config)
                VALUES (?, ?, ?, ?, ?)
            """, (
                name,
                company["id"],
                json.dumps(peer_ids),
                schedule,
                json.dumps(config or {}),
            ))
            conn.commit()
            return cursor.lastrowid

    def get_scheduled_reports(self) -> list[dict]:
        """Get all scheduled reports."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT sr.*, c.name as company_name, c.url as company_url
                FROM scheduled_reports sr
                JOIN companies c ON sr.company_id = c.id
                WHERE sr.enabled = 1
            """)

            reports = []
            for row in cursor.fetchall():
                report = dict(row)
                report["peer_company_ids"] = json.loads(report["peer_company_ids"])
                report["config"] = json.loads(report["config"]) if report["config"] else {}
                reports.append(report)

            return reports

    def update_scheduled_report_run(self, report_id: int, next_run: datetime) -> None:
        """Update last/next run times for a scheduled report."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE scheduled_reports
                SET last_run = CURRENT_TIMESTAMP, next_run = ?
                WHERE id = ?
            """, (next_run.isoformat(), report_id))
            conn.commit()
