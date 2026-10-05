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
            "",
            "These findings are evidence-based impact predictions. They do not by themselves prove "
            "that a runtime failure occurred or that a user flow was tested.",
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
                if result.verified_checks:
                    label = (
                        "Verified checks"
                        if result.status == "PASS"
                        else "Checks passed before stop"
                    )
                    lines.extend(
                        [f"  - {label}:", *[f"    - {check}" for check in result.verified_checks]]
                    )
        else:
            lines.append("- No behavior checks were run.")
        lines.extend(
            [
                "",
                "A PASS covers only the listed assertions; it does not mean the complete user flow "
                "was verified.",
            ]
        )

        lines.extend(["", "## Coverage gaps"])
        if report.gaps:
            lines.extend(f"- {gap}" for gap in report.gaps)
        else:
            lines.append(
                "- No gaps were recorded by the configured stages; this does not prove complete coverage."
            )
        return "\n".join(lines).strip() + "\n"
