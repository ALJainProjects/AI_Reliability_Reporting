"""Data models for the reliability reporter."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, computed_field


class IncidentUpdate(BaseModel):
    """Individual update within an incident timeline."""

    id: str
    status: str  # investigating, identified, monitoring, resolved, postmortem
    body: str
    created_at: datetime


class AffectedComponent(BaseModel):
    """Service component affected by incident."""

    id: str
    name: str
    status: str | None = None  # operational, degraded_performance, partial_outage, major_outage


class Incident(BaseModel):
    """Core incident model representing a single reliability incident."""

    id: str
    name: str
    status: str  # investigating, identified, monitoring, resolved, postmortem
    impact: str  # none, minor, major, critical
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    started_at: datetime | None = None

    # Related data
    incident_updates: list[IncidentUpdate] = Field(default_factory=list)
    affected_components: list[AffectedComponent] = Field(default_factory=list)

    # Source metadata
    source_url: str
    company_name: str
    shortlink: str | None = None

    # AI-generated fields (populated during classification)
    category: str | None = None
    category_confidence: float | None = None
    summary: str | None = None
    root_cause: str | None = None

    @computed_field
    @property
    def duration_minutes(self) -> float | None:
        """Calculate incident duration in minutes."""
        if self.resolved_at and self.started_at:
            delta = self.resolved_at - self.started_at
            return delta.total_seconds() / 60
        elif self.resolved_at and self.created_at:
            delta = self.resolved_at - self.created_at
            return delta.total_seconds() / 60
        return None

    @computed_field
    @property
    def duration_hours(self) -> float | None:
        """Calculate incident duration in hours."""
        if self.duration_minutes:
            return self.duration_minutes / 60
        return None

    @computed_field
    @property
    def is_resolved(self) -> bool:
        """Check if incident is resolved."""
        return self.resolved_at is not None or self.status in ("resolved", "postmortem")

    def get_full_description(self) -> str:
        """Get full incident description from all updates."""
        parts = [self.name]
        for update in sorted(self.incident_updates, key=lambda x: x.created_at):
            if update.body:
                parts.append(f"[{update.status}] {update.body}")
        return "\n".join(parts)


class Category(BaseModel):
    """Incident category derived from cross-company analysis."""

    id: str  # Slug: e.g., "database-outage"
    name: str  # Display name: e.g., "Database Outage"
    description: str  # What types of incidents fall into this category
    keywords: list[str] = Field(default_factory=list)

    # Metadata (populated during analysis)
    incident_count: int = 0
    example_incidents: list[str] = Field(default_factory=list, max_length=5)


class StatusPage(BaseModel):
    """Metadata about a company's status page."""

    company_name: str
    base_url: str
    api_base_url: str | None = None

    # Detected capabilities
    has_api: bool = True
    has_rss: bool = False
    has_history_pages: bool = True

    # Components discovered
    components: list[AffectedComponent] = Field(default_factory=list)


class CompanyConfig(BaseModel):
    """Configuration for a company to analyze."""

    name: str
    url: str
    is_target: bool = False  # True for the main company, False for peers


class IncidentStats(BaseModel):
    """Statistical summary of incidents."""

    total_count: int = 0
    resolved_count: int = 0
    unresolved_count: int = 0

    # By impact level
    critical_count: int = 0
    major_count: int = 0
    minor_count: int = 0
    none_count: int = 0

    # By category
    by_category: dict[str, int] = Field(default_factory=dict)

    # Duration statistics (in hours)
    avg_duration_hours: float | None = None
    median_duration_hours: float | None = None
    min_duration_hours: float | None = None
    max_duration_hours: float | None = None

    # MTTR (Mean Time To Resolution) in hours
    mttr_hours: float | None = None


class TrendData(BaseModel):
    """Time-series trend data for a specific period."""

    period: str  # "2024-01", "2024-Q1", etc.
    period_start: datetime
    period_end: datetime

    incident_count: int = 0
    critical_count: int = 0
    major_count: int = 0

    avg_duration_hours: float | None = None
    total_downtime_hours: float = 0.0


class KeyIssue(BaseModel):
    """AI-identified key reliability issue."""

    issue: str
    frequency: str
    trend: str  # "improving", "stable", "worsening"
    impact: str
    recommendation: str | None = None


class PeerComparison(BaseModel):
    """Comparison data between target company and a peer."""

    peer_name: str
    peer_incident_count: int
    peer_mttr_hours: float | None = None
    peer_critical_count: int = 0

    # Relative metrics
    incident_count_diff: int = 0  # positive = target has more
    mttr_diff_hours: float | None = None  # positive = target is slower


class Report(BaseModel):
    """Full reliability report for a company."""

    # Metadata
    company_name: str
    peer_companies: list[str] = Field(default_factory=list)
    start_date: datetime
    end_date: datetime
    generated_at: datetime = Field(default_factory=datetime.now)

    # Core data
    incidents: list[Incident] = Field(default_factory=list)
    categories: list[Category] = Field(default_factory=list)

    # Analysis
    stats: IncidentStats = Field(default_factory=IncidentStats)
    trends: list[TrendData] = Field(default_factory=list)
    key_issues: list[KeyIssue] = Field(default_factory=list)

    # Peer comparison (optional)
    peer_comparisons: list[PeerComparison] = Field(default_factory=list)

    @computed_field
    @property
    def total_incidents(self) -> int:
        """Total number of incidents in the report."""
        return len(self.incidents)

    @computed_field
    @property
    def timeframe_days(self) -> int:
        """Number of days in the analysis timeframe."""
        return (self.end_date - self.start_date).days
