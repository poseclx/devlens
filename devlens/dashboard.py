"""Web Dashboard generator — static HTML with interactive charts.

Produces a single self-contained HTML file that visualises every DevLens
analysis dimension: code review, security scan, complexity metrics,
dependency audit, documentation health, and custom rule violations.

No server required — open the HTML file in any browser.
Uses Chart.js (CDN) for graphs and vanilla JS for filtering / theme toggle.
"""

from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _safe_import(module: str, attr: str):
    """Return *module.attr* or ``None`` when unavailable."""
    try:
        mod = __import__(f"devlens.{module}", fromlist=[attr])
        return getattr(mod, attr, None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class MetricCard:
    """A single KPI card shown at the top of the dashboard."""
    label: str
    value: str | int | float
    icon: str = ""
    trend: str = ""
    color: str = ""


@dataclass
class SectionData:
    """One collapsible section inside the dashboard."""
    id: str
    title: str
    icon: str = ""
    summary: str = ""
    table_headers: list[str] = field(default_factory=list)
    table_rows: list[list[str]] = field(default_factory=list)
    chart_type: str = ""
    chart_data: dict = field(default_factory=dict)


@dataclass
class DashboardData:
    """Everything the HTML template needs."""
    project_name: str = ""
    generated_at: str = ""
    devlens_version: str = "0.6.0"
    cards: list[MetricCard] = field(default_factory=list)
    sections: list[SectionData] = field(default_factory=list)
    raw_json: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )


# ---------------------------------------------------------------------------
# Metric collection helpers
# ---------------------------------------------------------------------------

def _collect_complexity(path: str, **kw) -> tuple[list[MetricCard], SectionData | None]:
    """Run complexity analysis and return cards + section."""
    analyze_path = _safe_import("complexity", "analyze_path")
    if analyze_path is None:
        return [], None
    try:
        report = analyze_path(path, **kw)
    except Exception:
        return [], None

    cards = [
        MetricCard(
            label="Avg Complexity",
            value=round(report.avg_complexity, 1),
            icon="\u27e1",
            color="#6366f1" if report.avg_complexity < 10 else "#ef4444",
        ),
        MetricCard(
            label="Hotspots",
            value=len(report.hotspots) if hasattr(report, "hotspots") else 0,
            icon="\u2622",
            color="#f59e0b",
        ),
    ]

    rows = []
    for fc in report.files:
        for fn in fc.functions:
            rows.append([
                html.escape(fc.filepath),
                html.escape(fn.name),
                str(fn.complexity),
                str(fn.loc),
                fn.grade,
            ])
    rows.sort(key=lambda r: int(r[2]), reverse=True)

    chart_labels = [r[1][:30] for r in rows[:15]]
    chart_values = [int(r[2]) for r in rows[:15]]

    section = SectionData(
        id="complexity",
        title="Complexity Analysis",
        icon="\u27e1",
        summary=f"{len(report.files)} files, avg {report.avg_complexity:.1f}, grade {report.overall_grade}",
        table_headers=["File", "Function", "Complexity", "LOC", "Grade"],
        table_rows=rows[:50],
        chart_type="bar",
        chart_data={
            "labels": chart_labels,
            "datasets": [{
                "label": "Cyclomatic Complexity",
                "data": chart_values,
                "backgroundColor": [
                    "#ef4444" if v >= 20 else "#f59e0b" if v >= 10 else "#22c55e"
                    for v in chart_values
                ],
            }],
        },
    )
    return cards, section


def _collect_security(path: str, **kw) -> tuple[list[MetricCard], SectionData | None]:
    """Run security scan and return cards + section."""
    scan_path = _safe_import("security", "scan_path")
    if scan_path is None:
        return [], None
    try:
        findings = scan_path(path, **kw)
    except Exception:
        return [], None

    sev_counts: dict[str, int] = {}
    for f in findings:
        s = f.severity if isinstance(f.severity, str) else f.severity.value
        sev_counts[s] = sev_counts.get(s, 0) + 1

    cards = [
        MetricCard(
            label="Security Issues",
            value=len(findings),
            icon="\u26a0",
            color="#ef4444" if len(findings) > 0 else "#22c55e",
        ),
    ]

    rows = []
    for f in findings:
        sev = f.severity if isinstance(f.severity, str) else f.severity.value
        rows.append([
            html.escape(f.rule_id),
            sev,
            html.escape(f.title),
            html.escape(getattr(f, "file", "") or ""),
            str(getattr(f, "line", "")),
        ])

    sev_labels = list(sev_counts.keys()) or ["None"]
    sev_values = list(sev_counts.values()) or [0]
    sev_colors = {
        "critical": "#dc2626", "high": "#f97316",
        "medium": "#eab308", "low": "#3b82f6", "info": "#9ca3af",
    }

    section = SectionData(
        id="security",
        title="Security Scan",
        icon="\u26a0",
        summary=f"{len(findings)} finding(s) -- {sev_counts}",
        table_headers=["Rule", "Severity", "Title", "File", "Line"],
        table_rows=rows[:80],
        chart_type="doughnut",
        chart_data={
            "labels": sev_labels,
            "datasets": [{
                "data": sev_values,
                "backgroundColor": [
                    sev_colors.get(s.lower(), "#9ca3af") for s in sev_labels
                ],
            }],
        },
    )
    return cards, section


def _collect_depaudit(path: str, **kw) -> tuple[list[MetricCard], SectionData | None]:
    """Run dependency audit and return cards + section."""
    audit_dependencies = _safe_import("depaudit", "audit_dependencies")
    if audit_dependencies is None:
        return [], None
    try:
        report = audit_dependencies(path, **kw)
    except Exception:
        return [], None

    vuln_count = sum(len(d.vulnerabilities) for d in report.dependencies)
    outdated = sum(1 for d in report.dependencies if d.latest_version and d.latest_version != d.version)

    cards = [
        MetricCard(label="Dependencies", value=len(report.dependencies), icon="\u26d3", color="#6366f1"),
        MetricCard(label="Vulnerabilities", value=vuln_count, icon="\u2620",
                   color="#ef4444" if vuln_count else "#22c55e"),
    ]

    rows = []
    for d in report.dependencies:
        status = "OK"
        if d.vulnerabilities:
            status = f"{len(d.vulnerabilities)} vuln(s)"
        elif d.latest_version and d.latest_version != d.version:
            status = "Outdated"
        rows.append([
            html.escape(d.name),
            html.escape(d.version or "?"),
            html.escape(d.latest_version or "-"),
            html.escape(d.ecosystem),
            status,
        ])

    section = SectionData(
        id="dependencies",
        title="Dependency Audit",
        icon="\u26d3",
        summary=f"{len(report.dependencies)} deps, {vuln_count} vuln(s), {outdated} outdated",
        table_headers=["Package", "Version", "Latest", "Ecosystem", "Status"],
        table_rows=rows,
    )
    return cards, section


def _collect_docs(path: str, **kw) -> tuple[list[MetricCard], SectionData | None]:
    """Run docs check and return cards + section."""
    check_docs = _safe_import("docs_checker", "check_docs")
    if check_docs is None:
        return [], None

    target = Path(path)
    md_files = list(target.rglob("*.md")) if target.is_dir() else [target]
    if not md_files:
        return [], None

    all_issues: list[tuple[str, Any]] = []
    total_blocks = 0
    for md in md_files[:20]:
        try:
            result = check_docs(str(md))
            total_blocks += len(result.blocks)
            for iss in result.issues:
                all_issues.append((str(md), iss))
        except Exception:
            continue

    cards = [
        MetricCard(label="Docs Files", value=len(md_files), icon="\u2709", color="#6366f1"),
        MetricCard(label="Docs Issues", value=len(all_issues), icon="\u270d",
                   color="#f59e0b" if all_issues else "#22c55e"),
    ]

    rows = []
    for fpath, iss in all_issues:
        rows.append([
            html.escape(str(fpath)),
            str(getattr(iss, "block_index", "")),
            html.escape(getattr(iss, "issue_type", "")),
            html.escape(getattr(iss, "message", "")),
        ])

    section = SectionData(
        id="docs",
        title="Documentation Health",
        icon="\u2709",
        summary=f"{len(md_files)} file(s), {total_blocks} code blocks, {len(all_issues)} issue(s)",
        table_headers=["File", "Block", "Type", "Message"],
        table_rows=rows[:50],
    )
    return cards, section


def _collect_rules(path: str, config: dict | None = None, **kw) -> tuple[list[MetricCard], SectionData | None]:
    """Run custom rules and return cards + section."""
    RuleEngine = _safe_import("rules", "RuleEngine")
    if RuleEngine is None:
        return [], None
    try:
        engine = RuleEngine(config=config or {})
        violations = engine.evaluate_path(path)
    except Exception:
        return [], None

    sev_counts: dict[str, int] = {}
    for v in violations:
        s = v.severity if isinstance(v.severity, str) else v.severity.value
        sev_counts[s] = sev_counts.get(s, 0) + 1

    cards = [
        MetricCard(label="Rule Violations", value=len(violations), icon="\u2696",
                   color="#ef4444" if violations else "#22c55e"),
    ]

    rows = []
    for v in violations:
        sev = v.severity if isinstance(v.severity, str) else v.severity.value
        rows.append([
            html.escape(v.rule_id),
            sev,
            html.escape(v.message),
            html.escape(v.filepath),
            str(v.line),
        ])

    section = SectionData(
        id="rules",
        title="Custom Rules",
        icon="\u2696",
        summary=f"{len(violations)} violation(s) from {len(engine.rules)} rule(s)",
        table_headers=["Rule", "Severity", "Message", "File", "Line"],
        table_rows=rows[:80],
    )
    return cards, section


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def collect_project_metrics(
    path: str,
    *,
    config: dict | None = None,
    skip: set[str] | None = None,
) -> DashboardData:
    """Run all available analyses on *path* and return a DashboardData.

    Parameters
    ----------
    path : str
        Project root directory.
    config : dict, optional
        Merged DevLens config (from load_config).
    skip : set[str], optional
        Section IDs to skip (e.g. {"docs", "rules"}).
    """
    skip = skip or set()
    project = Path(path)
    data = DashboardData(project_name=project.resolve().name)
    raw: dict[str, Any] = {}

    collectors = [
        ("complexity", _collect_complexity),
        ("security", _collect_security),
        ("dependencies", _collect_depaudit),
        ("docs", _collect_docs),
        ("rules", _collect_rules),
    ]

    for section_id, collector_fn in collectors:
        if section_id in skip:
            continue
        kw: dict[str, Any] = {}
        if section_id == "rules":
            kw["config"] = config
        try:
            cards, section = collector_fn(path, **kw)
        except Exception:
            continue
        data.cards.extend(cards)
        if section is not None:
            data.sections.append(section)
            raw[section_id] = {
                "summary": section.summary,
                "row_count": len(section.table_rows),
            }

    data.raw_json = raw
    return data


# ---------------------------------------------------------------------------
# HTML generator
# ---------------------------------------------------------------------------

_CSS = """:root {
  --bg: #0f172a; --bg2: #1e293b; --fg: #e2e8f0; --fg2: #94a3b8;
  --accent: #6366f1; --green: #22c55e; --red: #ef4444;
  --yellow: #eab308; --orange: #f97316; --blue: #3b82f6;
  --radius: 12px; --shadow: 0 4px 24px rgba(0,0,0,.4);
}
[data-theme="light"] {
  --bg: #f8fafc; --bg2: #ffffff; --fg: #1e293b; --fg2: #64748b;
  --shadow: 0 4px 24px rgba(0,0,0,.08);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--bg); color: var(--fg);
  line-height: 1.6; padding: 2rem;
}
.container { max-width: 1280px; margin: 0 auto; }
.header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 2rem; flex-wrap: wrap; gap: 1rem;
}
.header h1 { font-size: 1.75rem; font-weight: 700; }
.header h1 span { color: var(--accent); }
.header-meta { color: var(--fg2); font-size: .85rem; }
.theme-btn {
  background: var(--bg2); border: 1px solid var(--fg2); color: var(--fg);
  padding: .4rem .8rem; border-radius: 8px; cursor: pointer; font-size: .85rem;
}
.cards {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 1rem; margin-bottom: 2rem;
}
.card {
  background: var(--bg2); border-radius: var(--radius); padding: 1.2rem;
  box-shadow: var(--shadow); text-align: center;
}
.card .icon { font-size: 1.6rem; margin-bottom: .4rem; }
.card .value { font-size: 1.8rem; font-weight: 700; }
.card .label { color: var(--fg2); font-size: .8rem; text-transform: uppercase; letter-spacing: .05em; }
.section {
  background: var(--bg2); border-radius: var(--radius); padding: 1.5rem;
  margin-bottom: 1.5rem; box-shadow: var(--shadow);
}
.section-header {
  display: flex; justify-content: space-between; align-items: center;
  cursor: pointer; user-select: none;
}
.section-header h2 { font-size: 1.2rem; font-weight: 600; }
.section-header .toggle { font-size: 1.2rem; transition: transform .2s; }
.section-header .toggle.collapsed { transform: rotate(-90deg); }
.section-body { margin-top: 1rem; }
.section-body.hidden { display: none; }
.section-summary { color: var(--fg2); font-size: .85rem; margin-bottom: 1rem; }
.tbl-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: .85rem; }
th { text-align: left; padding: .6rem .8rem; border-bottom: 2px solid var(--accent); color: var(--fg2); font-weight: 600; }
td { padding: .5rem .8rem; border-bottom: 1px solid rgba(148,163,184,.15); }
tr:hover { background: rgba(99,102,241,.06); }
.chart-wrap { max-width: 600px; margin: 1rem auto; }
.filter-bar {
  display: flex; gap: .6rem; margin-bottom: 1.5rem; flex-wrap: wrap;
}
.filter-btn {
  background: var(--bg2); border: 1px solid var(--fg2); color: var(--fg);
  padding: .35rem .8rem; border-radius: 8px; cursor: pointer; font-size: .8rem;
  transition: all .15s;
}
.filter-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; }
.sev { padding: .15rem .5rem; border-radius: 4px; font-size: .75rem; font-weight: 600; text-transform: uppercase; }
.sev-critical { background: #dc2626; color: #fff; }
.sev-high     { background: #f97316; color: #fff; }
.sev-medium   { background: #eab308; color: #000; }
.sev-low      { background: #3b82f6; color: #fff; }
.sev-info     { background: #9ca3af; color: #000; }
.grade { display: inline-block; width: 28px; height: 28px; line-height: 28px; text-align: center;
  border-radius: 50%; font-weight: 700; font-size: .8rem; }
.grade-a { background: #22c55e; color: #fff; }
.grade-b { background: #84cc16; color: #000; }
.grade-c { background: #eab308; color: #000; }
.grade-d { background: #f97316; color: #fff; }
.grade-f { background: #ef4444; color: #fff; }
@media (max-width: 640px) {
  body { padding: 1rem; }
  .cards { grid-template-columns: repeat(2, 1fr); }
  .header h1 { font-size: 1.3rem; }
}"""

_JS = """document.addEventListener('DOMContentLoaded', () => {
  const html = document.documentElement;
  const btn = document.getElementById('themeToggle');
  const stored = localStorage.getItem('devlens-theme');
  if (stored) html.dataset.theme = stored;
  btn?.addEventListener('click', () => {
    const next = html.dataset.theme === 'light' ? 'dark' : 'light';
    html.dataset.theme = next;
    localStorage.setItem('devlens-theme', next);
    btn.textContent = next === 'light' ? 'Dark Mode' : 'Light Mode';
  });
  document.querySelectorAll('.section-header').forEach(h => {
    h.addEventListener('click', () => {
      const body = h.nextElementSibling;
      const icon = h.querySelector('.toggle');
      body.classList.toggle('hidden');
      icon?.classList.toggle('collapsed');
    });
  });
  document.querySelectorAll('.filter-btn').forEach(b => {
    b.addEventListener('click', () => {
      b.classList.toggle('active');
      const target = b.dataset.section;
      const sec = document.getElementById('section-' + target);
      if (sec) sec.style.display = b.classList.contains('active') ? '' : 'none';
    });
  });
  if (typeof Chart !== 'undefined') {
    document.querySelectorAll('[data-chart]').forEach(canvas => {
      const cfg = JSON.parse(canvas.dataset.chart);
      new Chart(canvas, {
        type: cfg.type,
        data: cfg.data,
        options: {
          responsive: true,
          plugins: {
            legend: { labels: { color: getComputedStyle(document.body).getPropertyValue('--fg').trim() }},
          },
          scales: cfg.type === 'bar' ? {
            y: { beginAtZero: true, ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,.1)' }},
            x: { ticks: { color: '#94a3b8', maxRotation: 45 }, grid: { display: false }},
          } : undefined,
        },
      });
    });
  }
});"""


def _render_card(card: MetricCard) -> str:
    style = f' style="color:{card.color}"' if card.color else ""
    return (
        f'<div class="card">'
        f'<div class="icon">{html.escape(card.icon)}</div>'
        f'<div class="value"{style}>{html.escape(str(card.value))}</div>'
        f'<div class="label">{html.escape(card.label)}</div>'
        f'</div>'
    )


def _severity_badge(text: str) -> str:
    low = text.lower()
    if low in ("critical", "high", "medium", "low", "info"):
        return f'<span class="sev sev-{low}">{html.escape(text)}</span>'
    return html.escape(text)


def _grade_badge(text: str) -> str:
    letter = text[0].lower() if text else "?"
    cls = f"grade-{letter}" if letter in "abcdf" else ""
    return f'<span class="grade {cls}">{html.escape(text)}</span>'


def _render_cell(header: str, value: str) -> str:
    h = header.lower()
    if h == "severity":
        return f"<td>{_severity_badge(value)}</td>"
    if h == "grade":
        return f"<td>{_grade_badge(value)}</td>"
    return f"<td>{html.escape(value)}</td>"


def _render_section(sec: SectionData) -> str:
    parts = [f'<div class="section" id="section-{sec.id}">']
    parts.append(
        f'<div class="section-header">'
        f'<h2>{html.escape(sec.icon)} {html.escape(sec.title)}</h2>'
        f'<span class="toggle">&#9660;</span></div>'
    )
    parts.append('<div class="section-body">')
    if sec.summary:
        parts.append(f'<div class="section-summary">{html.escape(sec.summary)}</div>')
    if sec.chart_type and sec.chart_data:
        chart_cfg = json.dumps({"type": sec.chart_type, "data": sec.chart_data})
        parts.append(
            f'<div class="chart-wrap">'
            f"<canvas data-chart='{chart_cfg}'></canvas>"
            f'</div>'
        )
    if sec.table_headers and sec.table_rows:
        parts.append('<div class="tbl-wrap"><table>')
        parts.append("<thead><tr>" + "".join(
            f"<th>{html.escape(h)}</th>" for h in sec.table_headers
        ) + "</tr></thead>")
        parts.append("<tbody>")
        for row in sec.table_rows:
            parts.append("<tr>" + "".join(
                _render_cell(sec.table_headers[i] if i < len(sec.table_headers) else "", cell)
                for i, cell in enumerate(row)
            ) + "</tr>")
        parts.append("</tbody></table></div>")
    parts.append("</div></div>")
    return "\n".join(parts)


def generate_dashboard_html(data: DashboardData) -> str:
    """Produce a self-contained HTML dashboard string."""
    cards_html = "\n".join(_render_card(c) for c in data.cards)
    filter_btns = "\n".join(
        f'<button class="filter-btn active" data-section="{s.id}">'
        f'{html.escape(s.icon)} {html.escape(s.title)}</button>'
        for s in data.sections
    )
    sections_html = "\n".join(_render_section(s) for s in data.sections)
    raw_dump = html.escape(json.dumps(data.raw_json, indent=2))

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DevLens Dashboard &mdash; {html.escape(data.project_name)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>{_CSS}</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <h1><span>DevLens</span> Dashboard</h1>
      <div class="header-meta">
        Project: <strong>{html.escape(data.project_name)}</strong> |
        Generated: {html.escape(data.generated_at)} |
        v{html.escape(data.devlens_version)}
      </div>
    </div>
    <button class="theme-btn" id="themeToggle">Light Mode</button>
  </div>
  <div class="cards">{cards_html}</div>
  <div class="filter-bar">{filter_btns}</div>
  {sections_html}
  <details style="margin-top:2rem">
    <summary style="cursor:pointer;color:var(--fg2)">Raw JSON metrics</summary>
    <pre style="background:var(--bg2);padding:1rem;border-radius:8px;overflow-x:auto;font-size:.8rem;margin-top:.5rem">{raw_dump}</pre>
  </details>
</div>
<script>{_JS}</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# File export
# ---------------------------------------------------------------------------

def export_dashboard(
    path: str,
    output: str = "devlens-dashboard.html",
    *,
    config: dict | None = None,
    skip: set[str] | None = None,
) -> str:
    """One-shot: collect metrics + generate HTML + write file.

    Returns the absolute path of the written file.
    """
    data = collect_project_metrics(path, config=config, skip=skip)
    content = generate_dashboard_html(data)
    out = Path(output)
    out.write_text(content, encoding="utf-8")
    return str(out.resolve())
