# AI Reliability Report Generator

An AI-powered CLI tool that generates comprehensive reliability reports by analyzing status page incidents from enterprise services. Uses GPT-4 or Claude to intelligently categorize incidents, identify patterns, and provide actionable insights.

## Features

- **AI-Powered Categorization**: Automatically generates incident categories from analyzing ALL incidents, then classifies each incident with AI-generated summaries
- **Multi-Source Data Fetching**: Supports Statuspage.io API and HTML scraping for historical data
- **Generic Scraper**: Works with non-Statuspage.io status pages using heuristic detection
- **Multiple Output Formats**: Markdown reports, CSV, Excel spreadsheets, and PDF
- **Peer Comparison**: Compare reliability metrics across multiple companies
- **Web UI**: Interactive web interface for generating and viewing reports
- **Scheduled Monitoring**: Automated report generation with cron-based scheduling
- **Alerting**: Configurable alerts for incident thresholds, MTTR, and critical events
- **Slack/Email Notifications**: Automated delivery of reports and alerts
- **Historical Database**: SQLite storage for trend analysis over time
- **Custom Category Training**: Fine-tune categories based on user feedback

## Installation

```bash
# Clone the repository
git clone https://github.com/ALJainProjects/AI_Reliability_Reporting.git
cd AI_Reliability_Reporting

# Install the package
pip install -e .

# Copy and configure environment variables
cp .env.example .env
# Edit .env with your API keys
```

## Quick Start

### Generate a Report (with AI)

```bash
# Set your OpenAI API key
export OPENAI_API_KEY="sk-your-key-here"

# Generate a full AI-powered report
reliability-report generate \
  --company "Datadog" \
  --url "https://status.datadoghq.com" \
  --start-date "2024-01-01" \
  --pdf
```

### Generate a Report (without AI)

```bash
# Use default categories and heuristic classification
reliability-report generate \
  --company "GitHub" \
  --url "https://www.githubstatus.com" \
  --start-date "2024-06-01" \
  --skip-ai
```

### Compare with Peers

Create a `peers.json` file:
```json
[
  {"name": "PagerDuty", "url": "https://status.pagerduty.com"},
  {"name": "Splunk", "url": "https://status.splunk.com"}
]
```

Then run:
```bash
reliability-report generate \
  --company "Datadog" \
  --url "https://status.datadoghq.com" \
  --peers peers.json \
  --start-date "2024-01-01"
```

## CLI Commands

```bash
# Generate reliability report
reliability-report generate [OPTIONS]

# Start web UI server
reliability-report serve --port 8000

# Initialize database for historical tracking
reliability-report init-db --db-path ./reliability.db

# Start scheduled report daemon
reliability-report scheduler --db-path ./reliability.db

# Test a status page URL
reliability-report test-fetch --url "https://status.example.com"

# List configured AI providers
reliability-report list-providers
```

### Generate Command Options

| Option | Description |
|--------|-------------|
| `-c, --company` | Target company name (required) |
| `-u, --url` | Status page URL (required) |
| `-p, --peers` | JSON file with peer companies |
| `-s, --start-date` | Start date YYYY-MM-DD (required) |
| `-e, --end-date` | End date (default: today) |
| `-o, --output-dir` | Output directory (default: ./reports) |
| `--provider` | AI provider: openai or anthropic |
| `--skip-ai` | Use heuristic classification |
| `--pdf` | Generate PDF report |
| `-v, --verbose` | Verbose output |

## Web UI

Start the web server:
```bash
reliability-report serve --port 8000
```

Then open http://localhost:8000 to:
- Generate reports interactively
- View and download past reports
- Configure scheduled reports

## Output Files

Each report generates:
- `{company}_reliability_{date}.md` - Markdown report
- `{company}_reliability_{date}.csv` - CSV with all incidents
- `{company}_reliability_{date}.xlsx` - Excel with multiple sheets
- `{company}_reliability_{date}.pdf` - PDF report (with --pdf flag)
- `{company}_reliability_{date}_categories.json` - Category definitions

## Report Contents

### Executive Summary
- Total incidents and incident rate
- MTTR (Mean Time to Resolution)
- Impact distribution (Critical/Major/Minor)

### Detailed Analysis
- Monthly trend data
- Incidents by category
- Duration statistics
- Resolution metrics

### Key Issues
- Top reliability concerns
- Trend analysis (improving/stable/worsening)
- Actionable recommendations

### Peer Comparison
- Side-by-side metrics
- Relative performance analysis

## Supported Status Pages

### Native Support (Statuspage.io)
- GitHub, Datadog, New Relic, PagerDuty, Atlassian, Cloudflare, etc.

### Generic Scraper
- Any status page with incident history
- Heuristic detection of incident data

## Configuration

### Environment Variables

```bash
# Required for AI features (at least one)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Optional
AI_PROVIDER=openai  # or anthropic
OPENAI_MODEL=gpt-4o
ANTHROPIC_MODEL=claude-sonnet-4-20250514
```

### Notification Setup

For Slack notifications:
```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
```

For email notifications:
```bash
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
```

## Architecture

```
reliability-report-generator/
├── src/reliability_reporter/
│   ├── cli.py                 # CLI commands
│   ├── config.py              # Configuration
│   ├── models.py              # Data models
│   ├── fetchers/              # Data fetching
│   │   ├── api_fetcher.py     # Statuspage.io API
│   │   ├── html_scraper.py    # HTML fallback
│   │   └── generic_scraper.py # Non-Statuspage sites
│   ├── categorization/        # AI categorization
│   │   ├── ai_client.py       # OpenAI/Anthropic
│   │   ├── category_generator.py
│   │   ├── classifier.py
│   │   └── training.py        # Custom training
│   ├── analysis/              # Statistics & trends
│   ├── reporters/             # Output generation
│   │   ├── markdown_reporter.py
│   │   ├── spreadsheet_reporter.py
│   │   └── pdf_reporter.py
│   ├── database/              # Persistence
│   │   ├── db.py              # SQLite storage
│   │   └── scheduler.py       # Automated reports
│   └── web/                   # Web UI
│       ├── app.py             # FastAPI app
│       └── templates/         # HTML templates
```

## Example Output

### AI-Generated Categories (from Datadog analysis)

| Category | Count | Description |
|----------|-------|-------------|
| Data Ingestion Delay | 22 | Delays in processing metrics, logs, traces |
| Monitoring Alerts Delay | 9 | Delayed alert evaluation/notification |
| Web Application Issues | 6 | Dashboard/UI availability problems |
| Authentication Issues | 4 | Login and SSO failures |
| Network Connectivity | 2 | Packet loss, latency issues |

### Sample Metrics

- **Incident Rate**: 3.1/month
- **MTTR**: 3.8 hours
- **Resolution Rate**: 100%
- **Critical Incidents**: 4.4%

## Future Directions

### Advanced Report Generation
- **Interactive HTML Reports**: Rich, client-side rendered reports with sortable tables, expandable incident details, and embedded visualizations using D3.js or Plotly
- **Executive Dashboard**: One-page summary view optimized for C-level stakeholders with key metrics, sparklines, and traffic-light indicators
- **Custom Report Templates**: User-defined Jinja2 templates for company-specific branding and formatting requirements
- **Multi-Language Support**: Localized reports for international teams (i18n)

### Intelligent Insights & Recommendations
- **Root Cause Analysis**: AI-powered correlation of incidents to identify common failure patterns and systemic issues
- **Predictive Reliability Scoring**: ML models to forecast future incident likelihood based on historical patterns
- **Automated Remediation Suggestions**: Context-aware recommendations based on incident category and company tech stack
- **SLA/SLO Tracking**: Calculate and visualize uptime percentages, error budgets, and SLA compliance

### Enhanced Monitoring & Alerting
- **Real-Time Status Monitoring**: Continuous polling with webhook notifications for new incidents
- **Anomaly Detection**: Statistical analysis to detect unusual incident patterns or frequency spikes
- **Custom Alert Rules**: Complex alert conditions (e.g., "3+ major incidents in 24h" or "MTTR > 4h for critical")
- **PagerDuty/OpsGenie Integration**: Direct incident escalation to on-call teams

### Data & Integration
- **Additional Data Sources**: Support for Instatus, Cachet, Sorry, and custom status page formats
- **API Endpoints**: RESTful API for programmatic access to reports and metrics
- **Webhook Receivers**: Ingest real-time incident data from status page webhooks
- **Data Export**: Integration with BI tools (Tableau, Looker, PowerBI) via standardized exports
- **Incident Enrichment**: Cross-reference with internal ticketing systems (Jira, ServiceNow) for fuller context

### Collaboration & Workflow
- **Report Annotations**: Allow users to add comments and context to specific incidents or trends
- **Report Sharing**: Generate shareable links with optional authentication
- **Diff Reports**: Compare reliability between two time periods to show improvement/regression
- **Incident Tagging**: Custom tags for filtering and grouping (e.g., "post-deployment", "third-party", "capacity")

### Infrastructure & Deployment
- **Docker Container**: Pre-built image for easy deployment
- **Kubernetes Helm Chart**: Production-ready deployment with horizontal scaling
- **GitHub Actions Workflow**: Automated scheduled reports committed to a repo or posted to Slack
- **Multi-Tenant Support**: Isolated data and configuration for multiple teams/organizations

### Testing & Reliability
- **Comprehensive Test Suite**: Unit tests with mocked APIs, integration tests with fixture data
- **Chaos Testing**: Validate graceful degradation when APIs are unavailable
- **Performance Benchmarks**: Load testing for large datasets (1000+ incidents)

## Contributing

Contributions welcome! Please open an issue or submit a pull request.

## License

MIT License

## Author

Arnav Jain
