"""Prompt templates for AI-powered categorization."""

CATEGORY_GENERATION_SYSTEM = """You are an expert Site Reliability Engineer (SRE) with deep experience in:
- Incident management and response
- Root cause analysis
- Building incident taxonomies for enterprise systems
- Comparing reliability across different companies

Your task is to analyze incidents from multiple companies and create a unified taxonomy of incident categories that enables meaningful cross-company comparison."""

CATEGORY_GENERATION_USER = """Analyze the following incidents from multiple enterprise status pages and generate a unified taxonomy of incident categories.

## Companies Analyzed:
{company_list}

## Total Incidents: {total_incidents}

## Sample Incidents (grouped by company):
{incidents_sample}

## Requirements:
1. Generate 8-15 mutually exclusive categories that cover the majority of incidents
2. Categories should be:
   - Technology-agnostic (applicable across different tech stacks)
   - Actionable (useful for root cause analysis)
   - Specific enough to be meaningful, but general enough to apply across companies
3. Each category needs:
   - A unique slug ID (lowercase, hyphenated)
   - A clear display name
   - A description of what incidents belong in this category
   - Keywords that help identify incidents in this category

## Common Category Examples (adapt based on actual incident patterns):
- Database/storage outages
- Network/connectivity issues
- Authentication/authorization failures
- API/service degradation
- Deployment/release issues
- Third-party service dependencies
- Infrastructure/cloud provider issues
- Performance/capacity problems
- Security incidents
- Configuration/DNS issues

## Output Format (JSON array):
```json
[
  {{
    "id": "database-outage",
    "name": "Database Outage",
    "description": "Complete or partial database unavailability, connection failures, or data access issues",
    "keywords": ["database", "db", "postgres", "mysql", "mongodb", "connection pool", "query timeout"]
  }}
]
```

Analyze the provided incidents and generate appropriate categories. Return ONLY the JSON array, no additional text."""

INCIDENT_CLASSIFICATION_SYSTEM = """You are an expert at analyzing incident reports and identifying root causes.
Your task is to classify incidents into predefined categories with high accuracy.
Focus on technical accuracy and extract the most relevant information from incident titles and updates."""

INCIDENT_CLASSIFICATION_USER = """Classify the following incident into one of the predefined categories.

## Available Categories:
{categories_json}

## Incident to Classify:
- **Title**: {incident_title}
- **Impact**: {incident_impact}
- **Status**: {incident_status}
- **Date**: {incident_date}

### Incident Updates/Timeline:
{incident_updates}

### Affected Components:
{affected_components}

## Instructions:
1. Read the incident details carefully
2. Match it to the MOST APPROPRIATE category from the list above
3. If multiple categories could apply, choose the PRIMARY root cause category
4. If no category fits well, use "other"
5. Generate a concise 1-2 sentence summary of what happened
6. Extract root cause if mentioned in updates (otherwise null)

## Output Format (JSON):
```json
{{
  "category_id": "database-outage",
  "confidence": 0.95,
  "summary": "PostgreSQL primary database experienced connection pool exhaustion, causing API timeouts for 47 minutes.",
  "root_cause": "Connection leak in user service microservice"
}}
```

Classify this incident and return ONLY the JSON object, no additional text."""

KEY_ISSUES_SYSTEM = """You are a reliability engineering expert analyzing incident trends to identify key issues and provide actionable recommendations.
Focus on patterns, trends over time, and areas that need attention."""

KEY_ISSUES_USER = """Analyze the incident data for {company_name} and identify key reliability issues.

## Analysis Period: {start_date} to {end_date}

## Incident Summary by Category:
{incidents_by_category}

## Monthly Trends:
{trends_data}

## Overall Statistics:
- Total Incidents: {total_incidents}
- Critical/Major Incidents: {severe_count}
- Average Resolution Time: {avg_resolution_hours} hours
- MTTR: {mttr_hours} hours

## Instructions:
Identify the top 3-5 most significant reliability issues. For each issue:
1. Describe the pattern (what's happening)
2. Quantify the frequency and impact
3. Identify if it's improving, stable, or worsening over time
4. Provide a specific recommendation

## Output Format (JSON array):
```json
[
  {{
    "issue": "Recurring database connection pool exhaustion",
    "frequency": "7 incidents in 3 months (2.3/month average)",
    "trend": "worsening",
    "impact": "Average 45 minutes downtime per incident, affecting all API services",
    "recommendation": "Review connection pool sizing and implement connection leak detection"
  }}
]
```

Analyze the data and return ONLY the JSON array, no additional text."""

BATCH_CLASSIFICATION_SYSTEM = """You are an expert at bulk incident classification.
Your task is to classify multiple incidents efficiently while maintaining accuracy.
Process each incident independently based on its content."""

BATCH_CLASSIFICATION_USER = """Classify the following batch of incidents into the predefined categories.

## Available Categories:
{categories_json}

## Incidents to Classify:
{incidents_batch}

## Instructions:
For each incident, provide:
1. The incident ID
2. The category ID it belongs to
3. A confidence score (0-1)
4. A brief summary (1-2 sentences)

## Output Format (JSON array):
```json
[
  {{
    "incident_id": "abc123",
    "category_id": "database-outage",
    "confidence": 0.92,
    "summary": "Database connection timeout affecting user logins."
  }}
]
```

Return ONLY the JSON array, no additional text."""
