"""Stable, deterministic Markdown formatting for completed impact reports."""

from impact_agent.domain.models import AgentReport
from impact_agent.report.interface.report_formatter import ReportFormatter


class MarkdownReportFormatter(ReportFormatter):
    def format(self, report: AgentReport) -> str:
        lines = [
            f"# Pull request impact report: {report.run_id}",
            "",
            f"**Status:** {report.status.value}",
            "",
            "## Summary",
            "",
            report.summary,
            "",
            "## Findings",
        ]
        if report.findings:
            for finding in report.findings:
                citations = ", ".join(finding.evidence_ids)
                lines.extend(
                    [
                        f"### {finding.title}",
                        "",
                        finding.explanation,
                        "",
                        f"Evidence: {citations}",
                        "",
                    ]
                )
        else:
            lines.extend(["", "No evidence-supported findings were produced.", ""])

        lines.append("## Behavior checks")
        if report.behavior_results:
            for result in report.behavior_results:
                lines.append(f"- **{result.status}** `{result.scenario_id}` — {result.summary}")
        else:
            lines.append("- No behavior checks were run.")

        lines.extend(["", "## Coverage gaps"])
        if report.gaps:
            lines.extend(f"- {gap}" for gap in report.gaps)
        else:
            lines.append("- None recorded.")
        return "\n".join(lines).strip() + "\n"
