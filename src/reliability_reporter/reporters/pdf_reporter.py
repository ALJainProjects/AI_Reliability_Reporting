"""Generate PDF reliability reports."""

import logging
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional

from ..models import Report

logger = logging.getLogger(__name__)


class PDFReporter:
    """Generate PDF format reliability reports using ReportLab."""

    def __init__(self):
        """Initialize the PDF reporter."""
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import letter
            from reportlab.platypus import SimpleDocTemplate
            self._reportlab_available = True
        except ImportError:
            self._reportlab_available = False
            logger.warning("reportlab not installed. Install with: pip install reportlab")

    def generate(self, report: Report, output_path: Path | str) -> Path:
        """
        Generate a PDF report.

        Args:
            report: Report object with all data
            output_path: Path to save the PDF

        Returns:
            Path to the generated PDF file
        """
        if not self._reportlab_available:
            raise ImportError("reportlab is required for PDF generation. Install with: pip install reportlab")

        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter, A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            PageBreak, Image, ListFlowable, ListItem
        )
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Create document
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72,
        )

        # Styles
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(
            name='ReportTitle',
            parent=styles['Heading1'],
            fontSize=24,
            spaceAfter=30,
            alignment=TA_CENTER,
        ))
        styles.add(ParagraphStyle(
            name='SectionTitle',
            parent=styles['Heading2'],
            fontSize=16,
            spaceBefore=20,
            spaceAfter=10,
            textColor=colors.HexColor('#1e40af'),
        ))
        styles.add(ParagraphStyle(
            name='SubSection',
            parent=styles['Heading3'],
            fontSize=12,
            spaceBefore=15,
            spaceAfter=5,
        ))
        styles.add(ParagraphStyle(
            name='Metric',
            parent=styles['Normal'],
            fontSize=10,
            spaceBefore=3,
            spaceAfter=3,
        ))

        # Build document content
        story = []

        # Title
        story.append(Paragraph(f"Reliability Report: {report.company_name}", styles['ReportTitle']))

        # Metadata
        meta_text = f"""
        <b>Analysis Period:</b> {report.start_date.strftime('%B %d, %Y')} - {report.end_date.strftime('%B %d, %Y')} ({report.timeframe_days} days)<br/>
        <b>Generated:</b> {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')}<br/>
        <b>Peer Companies:</b> {', '.join(report.peer_companies) if report.peer_companies else 'None'}
        """
        story.append(Paragraph(meta_text, styles['Normal']))
        story.append(Spacer(1, 20))

        # Executive Summary
        story.append(Paragraph("Executive Summary", styles['SectionTitle']))

        stats = report.stats
        if stats.total_count > 0:
            # Summary metrics
            mttr_str = f"{stats.mttr_hours:.1f} hours" if stats.mttr_hours else "N/A"
            incidents_per_month = stats.total_count / (report.timeframe_days / 30) if report.timeframe_days > 0 else 0

            summary_data = [
                ["Metric", "Value"],
                ["Total Incidents", str(stats.total_count)],
                ["Incident Rate", f"{incidents_per_month:.1f} /month"],
                ["MTTR", mttr_str],
                ["Resolved", f"{stats.resolved_count} ({stats.resolved_count/stats.total_count*100:.0f}%)"],
            ]

            summary_table = Table(summary_data, colWidths=[2.5*inch, 2*inch])
            summary_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 11),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8fafc')),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 10),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
                ('TOPPADDING', (0, 1), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
            ]))
            story.append(summary_table)
            story.append(Spacer(1, 15))

            # Impact Distribution
            story.append(Paragraph("Impact Distribution", styles['SubSection']))
            impact_data = [
                ["Impact Level", "Count", "Percentage"],
                ["Critical", str(stats.critical_count), f"{stats.critical_count/stats.total_count*100:.1f}%"],
                ["Major", str(stats.major_count), f"{stats.major_count/stats.total_count*100:.1f}%"],
                ["Minor", str(stats.minor_count), f"{stats.minor_count/stats.total_count*100:.1f}%"],
                ["None/Maintenance", str(stats.none_count), f"{stats.none_count/stats.total_count*100:.1f}%"],
            ]

            # Color-code impact rows
            impact_colors = {
                1: colors.HexColor('#fee2e2'),  # Critical - red
                2: colors.HexColor('#ffedd5'),  # Major - orange
                3: colors.HexColor('#fef9c3'),  # Minor - yellow
                4: colors.HexColor('#f0fdf4'),  # None - green
            }

            impact_table = Table(impact_data, colWidths=[2*inch, 1.5*inch, 1.5*inch])
            table_style = [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#374151')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ]
            for i, color in impact_colors.items():
                table_style.append(('BACKGROUND', (0, i), (-1, i), color))

            impact_table.setStyle(TableStyle(table_style))
            story.append(impact_table)

        else:
            story.append(Paragraph("No incidents recorded during the analysis period.", styles['Normal']))

        story.append(Spacer(1, 20))

        # Category Breakdown
        if stats.by_category:
            story.append(Paragraph("Incidents by Category", styles['SectionTitle']))

            cat_data = [["Category", "Count", "Percentage"]]
            sorted_cats = sorted(stats.by_category.items(), key=lambda x: -x[1])

            for cat_id, count in sorted_cats:
                category = next((c for c in report.categories if c.id == cat_id), None)
                name = category.name if category else cat_id.replace("-", " ").title()
                percentage = count / stats.total_count * 100
                cat_data.append([name, str(count), f"{percentage:.1f}%"])

            cat_table = Table(cat_data, colWidths=[3*inch, 1*inch, 1.5*inch])
            cat_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
                ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8fafc')),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
            ]))
            story.append(cat_table)
            story.append(Spacer(1, 20))

        # Trends
        if report.trends:
            story.append(Paragraph("Monthly Trends", styles['SectionTitle']))

            trend_data = [["Period", "Incidents", "Critical", "Major", "Avg Duration", "Downtime"]]
            for trend in report.trends:
                duration_str = f"{trend.avg_duration_hours:.1f}h" if trend.avg_duration_hours else "N/A"
                trend_data.append([
                    trend.period,
                    str(trend.incident_count),
                    str(trend.critical_count),
                    str(trend.major_count),
                    duration_str,
                    f"{trend.total_downtime_hours:.1f}h",
                ])

            trend_table = Table(trend_data, colWidths=[1*inch, 1*inch, 0.8*inch, 0.8*inch, 1*inch, 1*inch])
            trend_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            story.append(trend_table)
            story.append(Spacer(1, 20))

        # Key Issues
        if report.key_issues:
            story.append(Paragraph("Key Reliability Issues", styles['SectionTitle']))

            for i, issue in enumerate(report.key_issues, 1):
                trend_indicator = {
                    "improving": "(Improving)",
                    "stable": "(Stable)",
                    "worsening": "(Worsening)",
                }.get(issue.trend, "")

                story.append(Paragraph(
                    f"<b>{i}. {issue.issue}</b> <i>{trend_indicator}</i>",
                    styles['SubSection']
                ))
                story.append(Paragraph(f"<b>Frequency:</b> {issue.frequency}", styles['Metric']))
                story.append(Paragraph(f"<b>Impact:</b> {issue.impact}", styles['Metric']))
                if issue.recommendation:
                    story.append(Paragraph(f"<b>Recommendation:</b> {issue.recommendation}", styles['Metric']))
                story.append(Spacer(1, 10))

        # Peer Comparison
        if report.peer_comparisons:
            story.append(PageBreak())
            story.append(Paragraph("Peer Comparison", styles['SectionTitle']))

            peer_data = [["Company", "Incidents", "vs Target", "MTTR", "Critical"]]

            # Add target company
            mttr_str = f"{stats.mttr_hours:.1f}h" if stats.mttr_hours else "N/A"
            peer_data.append([
                f"{report.company_name} (Target)",
                str(stats.total_count),
                "-",
                mttr_str,
                str(stats.critical_count),
            ])

            for comp in report.peer_comparisons:
                diff_str = f"+{comp.incident_count_diff}" if comp.incident_count_diff > 0 else str(comp.incident_count_diff)
                peer_mttr = f"{comp.peer_mttr_hours:.1f}h" if comp.peer_mttr_hours else "N/A"
                peer_data.append([
                    comp.peer_name,
                    str(comp.peer_incident_count),
                    diff_str,
                    peer_mttr,
                    str(comp.peer_critical_count),
                ])

            peer_table = Table(peer_data, colWidths=[2*inch, 1*inch, 1*inch, 1*inch, 1*inch])
            peer_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#dbeafe')),
                ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
                ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ]))
            story.append(peer_table)

        # Footer
        story.append(Spacer(1, 30))
        story.append(Paragraph(
            f"<i>This report was generated automatically by the AI Reliability Report Generator.</i>",
            ParagraphStyle(name='Footer', parent=styles['Normal'], fontSize=8, textColor=colors.gray)
        ))

        # Build PDF
        doc.build(story)
        logger.info(f"Generated PDF report: {output_path}")

        return output_path

    def generate_to_bytes(self, report: Report) -> bytes:
        """Generate PDF to bytes (for web downloads)."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            temp_path = Path(f.name)

        try:
            self.generate(report, temp_path)
            return temp_path.read_bytes()
        finally:
            temp_path.unlink(missing_ok=True)
