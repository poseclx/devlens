"""GitHub API client — fetches PR data."""

from __future__ import annotations
import os
import httpx
from dataclasses import dataclass, field


GITHUB_API = "https://api.github.com"


@dataclass
class PRData:
    number: int
    title: str
    body: str
    author: str
    base_branch: str
    head_branch: str
    additions: int
    deletions: int
    changed_files: int
    files: list[dict] = field(default_factory=list)   # [{filename, status, patch, additions, deletions}]
    labels: list[str] = field(default_factory=list)
    linked_issues: list[str] = field(default_factory=list)


def _headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN")
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_pr(repo: str, pr_number: int) -> PRData:
    """Fetch PR metadata and file diffs from GitHub."""
    with httpx.Client(headers=_headers(), timeout=30) as client:
        pr_resp = client.get(f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}")
        pr_resp.raise_for_status()
        pr = pr_resp.json()

        files_resp = client.get(
            f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/files",
            params={"per_page": 100},
        )
        files_resp.raise_for_status()
        files = files_resp.json()

    return PRData(
        number=pr_number,
        title=pr["title"],
        body=pr.get("body") or "",
        author=pr["user"]["login"],
        base_branch=pr["base"]["ref"],
        head_branch=pr["head"]["ref"],
        additions=pr["additions"],
        deletions=pr["deletions"],
        changed_files=pr["changed_files"],
        files=[
            {
                "filename": f["filename"],
                "status": f["status"],
                "additions": f["additions"],
                "deletions": f["deletions"],
                "patch": f.get("patch", ""),
            }
            for f in files
        ],
        labels=[label["name"] for label in pr.get("labels", [])],
    )
