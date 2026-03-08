"""Team Scoreboard — metric storage, leaderboard, and trend tracking.

Records per-developer and per-project scores from DevLens analyses into
a lightweight JSON-based history store (.devlens-scores/).  Generates a
static HTML scoreboard with leaderboard tables and Chart.js trend graphs.

Usage (programmatic):
    from devlens.scoreboard import record_score, load_history, generate_scoreboard_html
    record_score(path=".", author="alice", metrics={...})
    history = load_history(path=".")
    html = generate_scoreboard_html(history)
"""

from __future__ import annotations

import html as _html
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class ScoreEntry:
    """A single recorded score snapshot."""
    timestamp: str = ""
    author: str = "unknown"
    project: str = ""
    pr_number: int | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    # metrics keys: reviews, complexity_avg, security_issues,
    #               rule_violations, docs_issues, deps_vulns, fix_count

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class ScoreHistory:
    """All recorded scores for a project."""
    project: str = ""
    scores_dir: str = ""
    entries: list[ScoreEntry] = field(default_factory=list)

    @property
    def authors(self) -> list[str]:
        return sorted({e.author for e in self.entries})

    @property
    def latest(self) -> ScoreEntry | None:
        return self.entries[-1] if self.entries else None


@dataclass
class LeaderboardRow:
    """Aggregated stats for one author."""
    author: str
    total_reviews: int = 0
    avg_complexity: float = 0.0
    total_security_issues: int = 0
    total_rule_violations: int = 0
    total_fixes: int = 0
    score: float = 0.0   # composite score


@dataclass
class TrendPoint:
    """Single point in a time-series trend."""
    timestamp: str
    value: float
    label: str = ""


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

DEFAULT_SCORES_DIR = ".devlens-scores"


def _scores_path(project_path: str, scores_dir: str | None = None) -> Path:
    """Resolve the scores directory for a project."""
    base = Path(project_path).resolve()
    dirname = scores_dir or DEFAULT_SCORES_DIR
    return base / dirname


def _history_file(scores: Path) -> Path:
    return scores / "history.json"


def record_score(
    path: str = ".",
    *,
    author: str = "unknown",
    pr_number: int | None = None,
    metrics: dict[str, Any] | None = None,
    scores_dir: str | None = None,
) -> ScoreEntry:
    """Append a new score entry to the project history.

    Parameters
    ----------
    path : str
        Project root directory.
    author : str
        Developer name / handle.
    pr_number : int, optional
        Associated PR number.
    metrics : dict
        Metric values to record (reviews, complexity_avg, etc.).
    scores_dir : str, optional
        Override scores directory name.

    Returns the created ScoreEntry.
    """
    scores = _scores_path(path, scores_dir)
    scores.mkdir(parents=True, exist_ok=True)

    project = Path(path).resolve().name

    entry = ScoreEntry(
        author=author,
        project=project,
        pr_number=pr_number,
        metrics=metrics or {},
    )

    hfile = _history_file(scores)
    data: list[dict] = []
    if hfile.exists():
        try:
            data = json.loads(hfile.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = []

    data.append(asdict(entry))
    hfile.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return entry


def load_history(
    path: str = ".",
    *,
    scores_dir: str | None = None,
) -> ScoreHistory:
    """Load all score entries for a project."""
    scores = _scores_path(path, scores_dir)
    hfile = _history_file(scores)

    entries: list[ScoreEntry] = []
    if hfile.exists():
        try:
            raw = json.loads(hfile.read_text(encoding="utf-8"))
            for item in raw:
                entries.append(ScoreEntry(**{
                    k: v for k, v in item.items()
                    if k in ScoreEntry.__dataclass_fields__
                }))
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    return ScoreHistory(
        project=Path(path).resolve().name,
        scores_dir=str(scores),
        entries=entries,
    )


def reset_history(
    path: str = ".",
    *,
    scores_dir: str | None = None,
) -> bool:
    """Delete all score history for a project. Returns True if deleted."""
    scores = _scores_path(path, scores_dir)
    hfile = _history_file(scores)
    if hfile.exists():
        hfile.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Aggregation & leaderboard
# ---------------------------------------------------------------------------

def _compute_composite_score(row: LeaderboardRow) -> float:
    """Compute a composite score (higher is better).

    Formula:  reviews * 10  +  fixes * 5  -  security_issues * 8
              -  rule_violations * 3  -  max(0, complexity_avg - 10) * 4
    """
    s = (
        row.total_reviews * 10
        + row.total_fixes * 5
        - row.total_security_issues * 8
        - row.total_rule_violations * 3
        - max(0.0, row.avg_complexity - 10) * 4
    )
    return round(s, 1)


def build_leaderboard(history: ScoreHistory) -> list[LeaderboardRow]:
    """Aggregate entries per author into a ranked leaderboard."""
    author_data: dict[str, dict[str, Any]] = {}

    for entry in history.entries:
        a = entry.author
        if a not in author_data:
            author_data[a] = {
                "reviews": 0, "complexity_sum": 0.0, "complexity_n": 0,
                "security": 0, "violations": 0, "fixes": 0,
            }
        m = entry.metrics
        author_data[a]["reviews"] += m.get("reviews", 1)
        if "complexity_avg" in m:
            author_data[a]["complexity_sum"] += m["complexity_avg"]
            author_data[a]["complexity_n"] += 1
        author_data[a]["security"] += m.get("security_issues", 0)
        author_data[a]["violations"] += m.get("rule_violations", 0)
        author_data[a]["fixes"] += m.get("fix_count", 0)

    rows: list[LeaderboardRow] = []
    for author, d in author_data.items():
        avg_c = d["complexity_sum"] / d["complexity_n"] if d["complexity_n"] else 0.0
        row = LeaderboardRow(
            author=author,
            total_reviews=d["reviews"],
            avg_complexity=round(avg_c, 1),
            total_security_issues=d["security"],
            total_rule_violations=d["violations"],
            total_fixes=d["fixes"],
        )
        row.score = _compute_composite_score(row)
        rows.append(row)

    rows.sort(key=lambda r: r.score, reverse=True)
    return rows


def calculate_trends(
    history: ScoreHistory,
    metric: str = "complexity_avg",
    *,
    author: str | None = None,
) -> list[TrendPoint]:
    """Extract a time-series of a specific metric.

    Parameters
    ----------
    history : ScoreHistory
    metric : str
        Key to extract from entry.metrics.
    author : str, optional
        Filter to a specific author.
    """
    points: list[TrendPoint] = []
    for entry in history.entries:
        if author and entry.author != author:
            continue
        val = entry.metrics.get(metric)
        if val is not None:
            points.append(TrendPoint(
                timestamp=entry.timestamp,
                value=float(val),
                label=entry.author,
            ))
    return points


# ---------------------------------------------------------------------------
# HTML scoreboard generator
# ---------------------------------------------------------------------------

_SCOREBOARD_CSS = """:root {
  --bg: #0f172a; --bg2: #1e293b; --fg: #e2e8f0; --fg2: #94a3b8;
  --accent: #8b5cf6; --gold: #fbbf24; --silver: #9ca3af; --bronze: #d97706;
  --radius: 12px; --shadow: 0 4px 24px rgba(0,0,0,.4);
}
[data-theme="light"] {
  --bg: #f8fafc; --bg2: #ffffff; --fg: #1e293b; --fg2: #64748b;
  --shadow: 0 4px 24px rgba(0,0,0,.08);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Inter', -apple-system, sans-serif;
  background: var(--bg); color: var(--fg); line-height: 1.6; padding: 2rem;
}
.container { max-width: 1100px; margin: 0 auto; }
.header { text-align: center; margin-bottom: 2rem; }
.header h1 { font-size: 2rem; font-weight: 700; }
.header h1 span { color: var(--accent); }
.header .meta { color: var(--fg2); font-size: .85rem; margin-top: .3rem; }
.theme-btn {
  background: var(--bg2); border: 1px solid var(--fg2); color: var(--fg);
  padding: .4rem .8rem; border-radius: 8px; cursor: pointer; font-size: .85rem;
  position: absolute; top: 1rem; right: 1rem;
}
.podium { display: flex; justify-content: center; gap: 1.5rem; margin: 2rem 0; flex-wrap: wrap; }
.podium-card {
  background: var(--bg2); border-radius: var(--radius); padding: 1.5rem 2rem;
  text-align: center; box-shadow: var(--shadow); min-width: 160px;
}
.podium-card .rank { font-size: 2rem; font-weight: 800; }
.podium-card .name { font-size: 1.1rem; font-weight: 600; margin: .3rem 0; }
.podium-card .pts { color: var(--fg2); font-size: .85rem; }
.rank-1 .rank { color: var(--gold); }
.rank-2 .rank { color: var(--silver); }
.rank-3 .rank { color: var(--bronze); }
.panel {
  background: var(--bg2); border-radius: var(--radius); padding: 1.5rem;
  margin-bottom: 1.5rem; box-shadow: var(--shadow);
}
.panel h2 { font-size: 1.15rem; font-weight: 600; margin-bottom: 1rem; }
table { width: 100%; border-collapse: collapse; font-size: .85rem; }
th { text-align: left; padding: .6rem .8rem; border-bottom: 2px solid var(--accent); color: var(--fg2); }
td { padding: .5rem .8rem; border-bottom: 1px solid rgba(148,163,184,.15); }
tr:hover { background: rgba(139,92,246,.06); }
.chart-wrap { max-width: 700px; margin: 1rem auto; }
.positive { color: #22c55e; }
.negative { color: #ef4444; }
@media (max-width: 640px) {
  body { padding: 1rem; }
  .podium { flex-direction: column; align-items: center; }
}"""

_SCOREBOARD_JS = """document.addEventListener('DOMContentLoaded', () => {
  const html = document.documentElement;
  const btn = document.getElementById('themeToggle');
  const stored = localStorage.getItem('devlens-sb-theme');
  if (stored) html.dataset.theme = stored;
  btn?.addEventListener('click', () => {
    const next = html.dataset.theme === 'light' ? 'dark' : 'light';
    html.dataset.theme = next;
    localStorage.setItem('devlens-sb-theme', next);
    btn.textContent = next === 'light' ? 'Dark Mode' : 'Light Mode';
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
          scales: {
            y: { beginAtZero: true, ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,.1)' }},
            x: { ticks: { color: '#94a3b8' }, grid: { display: false }},
          },
        },
      });
    });
  }
});"""


def _podium_html(rows: list[LeaderboardRow]) -> str:
    """Render top-3 podium cards."""
    if not rows:
        return ""
    medals = ["#1", "#2", "#3"]
    parts = ['<div class="podium">']
    for i, row in enumerate(rows[:3]):
        parts.append(
            f'<div class="podium-card rank-{i+1}">'
            f'<div class="rank">{medals[i]}</div>'
            f'<div class="name">{_html.escape(row.author)}</div>'
            f'<div class="pts">{row.score} pts</div>'
            f'</div>'
        )
    parts.append('</div>')
    return "\n".join(parts)


def _leaderboard_table_html(rows: list[LeaderboardRow]) -> str:
    """Render the full leaderboard table."""
    if not rows:
        return '<p style="color:var(--fg2)">No data recorded yet.</p>'
    headers = ["Rank", "Author", "Reviews", "Avg Complexity", "Security Issues",
               "Rule Violations", "Fixes", "Score"]
    parts = ['<div class="panel"><h2>Full Leaderboard</h2>']
    parts.append('<table><thead><tr>')
    for h in headers:
        parts.append(f'<th>{_html.escape(h)}</th>')
    parts.append('</tr></thead><tbody>')
    for i, row in enumerate(rows, 1):
        score_cls = "positive" if row.score >= 0 else "negative"
        parts.append(
            f'<tr>'
            f'<td><strong>{i}</strong></td>'
            f'<td>{_html.escape(row.author)}</td>'
            f'<td>{row.total_reviews}</td>'
            f'<td>{row.avg_complexity}</td>'
            f'<td>{row.total_security_issues}</td>'
            f'<td>{row.total_rule_violations}</td>'
            f'<td>{row.total_fixes}</td>'
            f'<td class="{score_cls}"><strong>{row.score}</strong></td>'
            f'</tr>'
        )
    parts.append('</tbody></table></div>')
    return "\n".join(parts)


def _trend_chart_html(history: ScoreHistory) -> str:
    """Render complexity trend chart."""
    points = calculate_trends(history, "complexity_avg")
    if not points:
        return ""

    labels = [p.timestamp[:10] for p in points]
    values = [p.value for p in points]

    chart_cfg = json.dumps({
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": "Avg Complexity Over Time",
                "data": values,
                "borderColor": "#8b5cf6",
                "backgroundColor": "rgba(139,92,246,.15)",
                "fill": True,
                "tension": 0.3,
            }],
        },
    })

    return (
        f'<div class="panel">'
        f'<h2>Complexity Trend</h2>'
        f'<div class="chart-wrap">'
        f"<canvas data-chart='{chart_cfg}'></canvas>"
        f'</div></div>'
    )


def _activity_chart_html(history: ScoreHistory) -> str:
    """Render per-author review activity bar chart."""
    author_counts: dict[str, int] = {}
    for e in history.entries:
        author_counts[e.author] = author_counts.get(e.author, 0) + 1

    if not author_counts:
        return ""

    labels = list(author_counts.keys())
    values = list(author_counts.values())
    colors = ["#8b5cf6", "#6366f1", "#3b82f6", "#22c55e", "#eab308",
              "#f97316", "#ef4444", "#ec4899"]

    chart_cfg = json.dumps({
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": "Total Entries",
                "data": values,
                "backgroundColor": [colors[i % len(colors)] for i in range(len(values))],
            }],
        },
    })

    return (
        f'<div class="panel">'
        f'<h2>Activity by Author</h2>'
        f'<div class="chart-wrap">'
        f"<canvas data-chart='{chart_cfg}'></canvas>"
        f'</div></div>'
    )


def generate_scoreboard_html(history: ScoreHistory) -> str:
    """Produce a self-contained HTML scoreboard."""
    rows = build_leaderboard(history)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    podium = _podium_html(rows)
    table = _leaderboard_table_html(rows)
    trend = _trend_chart_html(history)
    activity = _activity_chart_html(history)

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DevLens Scoreboard -- {_html.escape(history.project)}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>{_SCOREBOARD_CSS}</style>
</head>
<body>
<button class="theme-btn" id="themeToggle">Light Mode</button>
<div class="container">
  <div class="header">
    <h1><span>DevLens</span> Scoreboard</h1>
    <div class="meta">
      Project: <strong>{_html.escape(history.project)}</strong> |
      {len(history.entries)} entries | {len(history.authors)} authors |
      Generated: {_html.escape(now)}
    </div>
  </div>

  {podium}
  {table}
  {trend}
  {activity}

  <details style="margin-top:2rem">
    <summary style="cursor:pointer;color:var(--fg2)">Raw history JSON ({len(history.entries)} entries)</summary>
    <pre style="background:var(--bg2);padding:1rem;border-radius:8px;overflow-x:auto;font-size:.75rem;margin-top:.5rem">{_html.escape(json.dumps([asdict(e) for e in history.entries], indent=2))}</pre>
  </details>
</div>
<script>{_SCOREBOARD_JS}</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# File export
# ---------------------------------------------------------------------------

def export_scoreboard(
    path: str = ".",
    output: str = "devlens-scoreboard.html",
    *,
    scores_dir: str | None = None,
) -> str:
    """One-shot: load history + generate HTML + write file.

    Returns the absolute path of the written file.
    """
    history = load_history(path, scores_dir=scores_dir)
    content = generate_scoreboard_html(history)
    out = Path(output)
    out.write_text(content, encoding="utf-8")
    return str(out.resolve())
