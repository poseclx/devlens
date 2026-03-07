"""HTML report generator for all DevLens modules."""

from __future__ import annotations
from datetime import datetime
from pathlib import Path


_STYLES = """
:root {
  --bg: #0f1117;
  --surface: #1a1d27;
  --surface2: #22263a;
  --border: #2e3250;
  --accent: #6c8fff;
  --accent2: #a78bfa;
  --green: #34d399;
  --yellow: #fbbf24;
  --red: #f87171;
  --blue: #60a5fa;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --radius: 10px;
  --font: 'Inter', system-ui, sans-serif;
  --mono: 'JetBrains Mono', 'Fira Code', monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  font-size: 15px;
  line-height: 1.6;
  padding: 2rem;
  max-width: 960px;
  margin: 0 auto;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 2rem;
  padding-bottom: 1.5rem;
  border-bottom: 1px solid var(--border);
}
header h1 { font-size: 1.6rem; font-weight: 700; letter-spacing: -0.5px; }
header h1 span { color: var(--accent); }
.meta { color: var(--muted); font-size: 0.85rem; text-align: right; }
.badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.badge-green { background: rgba(52,211,153,.15); color: var(--green); }
.badge-yellow { background: rgba(251,191,36,.15); color: var(--yellow); }
.badge-red { background: rgba(248,113,113,.15); color: var(--red); }
.badge-blue { background: rgba(96,165,250,.15); color: var(--blue); }
.score-ring {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 2rem;
}
.ring-wrap { position: relative; width: 80px; height: 80px; }
.ring-wrap svg { transform: rotate(-90deg); }
.ring-num {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.1rem;
  font-weight: 700;
}
.ring-label h2 { font-size: 1.1rem; margin-bottom: 4px; }
.ring-label p { color: var(--muted); font-size: 0.9rem; }
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.25rem 1.5rem;
  margin-bottom: 1.25rem;
}
.card-title {
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--muted);
  margin-bottom: 0.75rem;
}
.issue-card { border-left: 3px solid var(--border); }
.issue-error { border-left-color: var(--red); }
.issue-warning { border-left-color: var(--yellow); }
.issue-info { border-left-color: var(--blue); }
.issue-header { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem; }
.issue-title { font-weight: 600; }
.issue-desc { color: var(--muted); font-size: 0.9rem; margin-bottom: 0.75rem; }
.issue-suggestion {
  background: var(--surface2);
  border-radius: 6px;
  padding: 0.6rem 0.9rem;
  font-size: 0.88rem;
  margin-bottom: 0.75rem;
}
.issue-suggestion strong { color: var(--green); }
pre {
  background: #0d0f18;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.9rem 1rem;
  overflow-x: auto;
  font-family: var(--mono);
  font-size: 0.82rem;
  line-height: 1.5;
  color: #c9d1d9;
  margin-top: 0.5rem;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 1rem;
  margin-bottom: 1.5rem;
}
.stat-box {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem 1.25rem;
  text-align: center;
}
.stat-box .num { font-size: 1.8rem; font-weight: 700; color: var(--accent); }
.stat-box .label { font-size: 0.78rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
.section-title {
  font-size: 1rem;
  font-weight: 600;
  margin: 2rem 0 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--border);
}
ul.recs { padding-left: 1.25rem; }
ul.recs li { margin-bottom: 0.4rem; color: var(--muted); font-size: 0.92rem; }
ul.recs li::marker { color: var(--accent); }
.file-chip {
  display: inline-block;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 2px 8px;
  font-family: var(--mono);
  font-size: 0.8rem;
  color: var(--accent2);
}
.tag-list { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.5rem; }
.tag {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 0.78rem;
  color: var(--muted);
}
footer {
  margin-top: 3rem;
  padding-top: 1rem;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.8rem;
  text-align: center;
}
"""


def _score_color(score: int) -> str:
    if score >= 80:
        return "#34d399"
    if score >= 50:
        return "#fbbf24"
    return "#f87171"


def _score_badge(score: int) -> str:
    if score >= 80:
        return "badge-green"
    if score >= 50:
        return "badge-yellow"
    return "badge-red"


def _severity_badge(severity: str) -> str:
    return {"error": "badge-red", "warning": "badge-yellow", "info": "badge-blue"}.get(severity, "badge-blue")


def _ring_svg(score: int) -> str:
    color = _score_color(score)
    r = 34
    circ = 2 * 3.14159 * r
    dash = circ * score / 100
    return f"""<svg width="80" height="80" viewBox="0 0 80 80">
      <circle cx="40" cy="40" r="{r}" fill="none" stroke="#22263a" stroke-width="8"/>
      <circle cx="40" cy="40" r="{r}" fill="none" stroke="{color}" stroke-width="8"
        stroke-dasharray="{dash:.1f} {circ:.1f}" stroke-linecap="round"/>
    </svg>"""


def _html_wrap(title: str, body: str, module: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DevLens \u2014 {title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{_STYLES}</style>
</head>
<body>
<header>
  <h1>Dev<span>Lens</span> &mdash; {title}</h1>
  <div class="meta">
    <div><span class="badge badge-blue">{module}</span></div>
    <div style="margin-top:4px">Generated {now}</div>
  </div>
</header>
{body}
<footer>Generated by <strong>DevLens</strong> &mdash; AI-powered developer assistant</footer>
</body>
</html>"""


# \u2500\u2500 Docs Health HTML \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def render_docs_html(result) -> str:
    score = result.health_score
    color = _score_color(score)
    badge = _score_badge(score)

    errors = sum(1 for i in result.issues if i.severity == "error")
    warnings = sum(1 for i in result.issues if i.severity == "warning")
    infos = sum(1 for i in result.issues if i.severity == "info")

    body = f"""
<div class="score-ring">
  <div class="ring-wrap">
    {_ring_svg(score)}
    <div class="ring-num" style="color:{color}">{score}</div>
  </div>
  <div class="ring-label">
    <h2>Docs Health Score <span class="badge {badge}">{score}/100</span></h2>
    <p class="file-chip">{result.file_path}</p>
  </div>
</div>

<div class="stat-grid">
  <div class="stat-box"><div class="num">{len(result.blocks)}</div><div class="label">Code Blocks</div></div>
  <div class="stat-box"><div class="num" style="color:var(--red)">{errors}</div><div class="label">Errors</div></div>
  <div class="stat-box"><div class="num" style="color:var(--yellow)">{warnings}</div><div class="label">Warnings</div></div>
  <div class="stat-box"><div class="num" style="color:var(--green)">{len(result.good_examples)}</div><div class="label">Good Examples</div></div>
</div>

<div class="card">
  <div class="card-title">Summary</div>
  <p>{result.summary}</p>
</div>
"""

    if result.issues:
        body += '<div class="section-title">Issues Found</div>'
        for issue in result.issues:
            cls = f"issue-{issue.severity}"
            sbadge = _severity_badge(issue.severity)
            code_html = f"<pre><code>{issue.code}</code></pre>" if issue.code else ""
            body += f"""
<div class="card issue-card {cls}">
  <div class="issue-header">
    <span class="badge {sbadge}">{issue.severity}</span>
    <span class="issue-title">{issue.title}</span>
    <span class="file-chip">{issue.language} \u00b7 block #{issue.block_index}</span>
  </div>
  <p class="issue-desc">{issue.description}</p>
  <div class="issue-suggestion"><strong>Fix:</strong> {issue.suggestion}</div>
  {code_html}
</div>"""

    if result.recommendations:
        body += '<div class="section-title">Recommendations</div><div class="card"><ul class="recs">'
        for rec in result.recommendations:
            body += f"<li>{rec}</li>"
        body += "</ul></div>"

    return _html_wrap(f"Docs Health \u2014 {Path(result.file_path).name}", body, "docs check")


# \u2500\u2500 PR Review HTML \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def render_pr_html(result, repo: str = "", pr_number: int = 0) -> str:
    score = result.risk_score if hasattr(result, "risk_score") else 0
    verdict = getattr(result, "verdict", "unknown")
    verdict_badge = {"approve": "badge-green", "request_changes": "badge-red", "comment": "badge-yellow"}.get(verdict, "badge-blue")

    risk_files = getattr(result, "risk_files", [])
    safe_files = getattr(result, "safe_files", [])
    summary = getattr(result, "summary", "")
    key_changes = getattr(result, "key_changes", [])
    suggestions = getattr(result, "suggestions", [])

    body = f"""
<div class="score-ring">
  <div class="ring-wrap">
    {_ring_svg(100 - score)}
    <div class="ring-num" style="color:{_score_color(100 - score)}">{score}<span style="font-size:0.6rem">risk</span></div>
  </div>
  <div class="ring-label">
    <h2>PR #{pr_number} &mdash; {repo} <span class="badge {verdict_badge}">{verdict.replace("_", " ")}</span></h2>
    <p style="color:var(--muted);font-size:0.9rem;margin-top:4px">{summary}</p>
  </div>
</div>

<div class="stat-grid">
  <div class="stat-box"><div class="num" style="color:var(--red)">{len(risk_files)}</div><div class="label">Risk Files</div></div>
  <div class="stat-box"><div class="num" style="color:var(--green)">{len(safe_files)}</div><div class="label">Safe Files</div></div>
  <div class="stat-box"><div class="num">{len(key_changes)}</div><div class="label">Key Changes</div></div>
  <div class="stat-box"><div class="num">{len(suggestions)}</div><div class="label">Suggestions</div></div>
</div>
"""

    if key_changes:
        body += '<div class="section-title">Key Changes</div><div class="card"><ul class="recs">'
        for ch in key_changes:
            body += f"<li>{ch}</li>"
        body += "</ul></div>"

    if risk_files:
        body += '<div class="section-title">Risk Files</div>'
        for rf in risk_files:
            fname = rf if isinstance(rf, str) else rf.get("file", "")
            reason = "" if isinstance(rf, str) else rf.get("reason", "")
            body += f"""<div class="card issue-card issue-error">
  <div class="issue-header"><span class="badge badge-red">risk</span><span class="file-chip">{fname}</span></div>
  {f'<p class="issue-desc">{reason}</p>' if reason else ""}
</div>"""

    if suggestions:
        body += '<div class="section-title">Suggestions</div><div class="card"><ul class="recs">'
        for s in suggestions:
            body += f"<li>{s}</li>"
        body += "</ul></div>"

    return _html_wrap(f"PR Review #{pr_number}", body, "review")


# \u2500\u2500 Onboarding HTML \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def render_onboard_html(result, repo_name: str = "") -> str:
    overview = getattr(result, "overview", "")
    architecture = getattr(result, "architecture", "")
    tech_stack = getattr(result, "tech_stack", [])
    entry_points = getattr(result, "entry_points", [])
    key_files = getattr(result, "key_files", [])
    getting_started = getattr(result, "getting_started", [])
    where_to_start = getattr(result, "where_to_start", "")

    body = f"""
<div class="card">
  <div class="card-title">Overview</div>
  <p>{overview}</p>
</div>

<div class="card">
  <div class="card-title">Architecture</div>
  <p>{architecture}</p>
</div>
"""

    if tech_stack:
        body += '<div class="card"><div class="card-title">Tech Stack</div><div class="tag-list">'
        for t in tech_stack:
            body += f'<span class="tag">{t}</span>'
        body += "</div></div>"

    if entry_points:
        body += '<div class="card"><div class="card-title">Entry Points</div><ul class="recs">'
        for ep in entry_points:
            body += f'<li><span class="file-chip">{ep}</span></li>'
        body += "</ul></div>"

    if key_files:
        body += '<div class="section-title">Key Files</div>'
        for kf in key_files:
            fname = kf.get("file", "") if isinstance(kf, dict) else kf
            role = kf.get("role", "") if isinstance(kf, dict) else ""
            body += f"""<div class="card">
  <div class="issue-header"><span class="file-chip">{fname}</span></div>
  <p class="issue-desc">{role}</p>
</div>"""

    if getting_started:
        body += '<div class="section-title">Getting Started</div><div class="card"><ol style="padding-left:1.25rem">'
        for step in getting_started:
            body += f"<li style='margin-bottom:0.4rem'>{step}</li>"
        body += "</ol></div>"

    if where_to_start:
        body += f"""<div class="card" style="border-left:3px solid var(--green)">
  <div class="card-title">Where to Start Reading</div>
  <p>{where_to_start}</p>
</div>"""

    return _html_wrap(f"Onboarding \u2014 {repo_name}", body, "onboard")


def save_html(html: str, output_path: str) -> None:
    Path(output_path).write_text(html, encoding="utf-8")
