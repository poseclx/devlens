"""Dependency auditor — parses lock/manifest files and checks OSV for known vulnerabilities."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Dependency:
    """A single resolved dependency."""

    name: str
    version: str
    ecosystem: str
    source_file: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "version": self.version,
            "ecosystem": self.ecosystem,
            "source": self.source_file,
        }


@dataclass
class Vulnerability:
    """A known vulnerability associated with a dependency."""

    id: str
    summary: str
    severity: str
    package: str
    version: str
    fixed_in: str = ""
    url: str = ""
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "summary": self.summary,
            "severity": self.severity,
            "package": self.package,
            "version": self.version,
            "fixed_in": self.fixed_in,
            "url": self.url,
            "aliases": list(self.aliases),
        }


@dataclass
class AuditReport:
    """Aggregated audit results for a set of dependencies."""

    dependencies: list[Dependency] = field(default_factory=list)
    vulnerabilities: list[Vulnerability] = field(default_factory=list)

    @property
    def critical_count(self) -> int:
        return sum(1 for v in self.vulnerabilities if v.severity == "critical")

    @property
    def high_count(self) -> int:
        return sum(1 for v in self.vulnerabilities if v.severity == "high")


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_requirements_txt(path: Path) -> list[Dependency]:
    """Parse a requirements.txt file into Dependency objects."""
    deps: list[Dependency] = []
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # Handle ==, >=, <=, ~=, !=, >, < version specifiers
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*([><=!~]+)\s*(.+)", line)
        if m:
            name, _op, version = m.group(1), m.group(2), m.group(3).strip()
            deps.append(Dependency(
                name=name,
                version=version,
                ecosystem="PyPI",
                source_file=str(path),
            ))
        else:
            # Bare package name without version
            name = line.split(";")[0].split("[")[0].strip()
            if name:
                deps.append(Dependency(
                    name=name,
                    version="*",
                    ecosystem="PyPI",
                    source_file=str(path),
                ))
    return deps


def _parse_package_json(path: Path) -> list[Dependency]:
    """Parse a package.json file into Dependency objects."""
    deps: list[Dependency] = []
    data = json.loads(path.read_text(encoding="utf-8"))
    for section in ("dependencies", "devDependencies"):
        for name, version in data.get(section, {}).items():
            # Strip leading ^ or ~ from semver ranges
            clean_version = version.lstrip("^~")
            deps.append(Dependency(
                name=name,
                version=clean_version,
                ecosystem="npm",
                source_file=str(path),
            ))
    return deps


def _parse_go_mod(path: Path) -> list[Dependency]:
    """Parse a go.mod file into Dependency objects."""
    deps: list[Dependency] = []
    text = path.read_text(encoding="utf-8")
    # Match lines like:  github.com/gin-gonic/gin v1.9.1
    pattern = re.compile(r"^\s+(\S+)\s+(v[\d.]+\S*)", re.MULTILINE)
    for m in pattern.finditer(text):
        module, version = m.group(1), m.group(2)
        deps.append(Dependency(
            name=module,
            version=version,
            ecosystem="Go",
            source_file=str(path),
        ))
    return deps


_PARSERS: dict[str, Any] = {
    "requirements.txt": _parse_requirements_txt,
    "package.json": _parse_package_json,
    "go.mod": _parse_go_mod,
}


def parse_dependencies(path: str) -> list[Dependency]:
    """Parse dependency files from a file or directory path.

    If *path* points to a directory, all recognized manifest files within it
    are parsed.  If it points to a single file, only that file is parsed.
    """
    p = Path(path)
    deps: list[Dependency] = []

    if p.is_dir():
        for filename, parser in _PARSERS.items():
            manifest = p / filename
            if manifest.exists():
                deps.extend(parser(manifest))
    elif p.is_file():
        parser = _PARSERS.get(p.name)
        if parser:
            deps.extend(parser(p))
    return deps


# ---------------------------------------------------------------------------
# OSV query helpers
# ---------------------------------------------------------------------------

_OSV_API = "https://api.osv.dev/v1/query"


def _query_osv(dep: Dependency) -> list[dict[str, Any]]:
    """Query the OSV.dev API for vulnerabilities affecting *dep*.

    Returns the ``vulns`` list from the response, or an empty list on error.
    """
    try:
        payload: dict[str, Any] = {
            "package": {"name": dep.name, "ecosystem": dep.ecosystem},
        }
        if dep.version and dep.version != "*":
            payload["version"] = dep.version
        resp = httpx.post(_OSV_API, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("vulns", [])
    except Exception:
        return []


def _severity_from_osv(vuln_data: dict[str, Any]) -> str:
    """Extract a human-readable severity string from an OSV vuln entry."""
    severity_list = vuln_data.get("severity", [])
    if not severity_list:
        return "unknown"
    score_str = severity_list[0].get("score", "0")
    try:
        score = float(score_str)
    except (ValueError, TypeError):
        return "unknown"
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    return "low"


def _fixed_version_from_osv(vuln_data: dict[str, Any]) -> str:
    """Extract the earliest fixed version from an OSV entry."""
    for affected in vuln_data.get("affected", []):
        for r in affected.get("ranges", []):
            for event in r.get("events", []):
                if "fixed" in event:
                    return event["fixed"]
    return ""


def _url_from_osv(vuln_data: dict[str, Any]) -> str:
    """Extract the first advisory URL from an OSV entry."""
    for ref in vuln_data.get("references", []):
        url = ref.get("url", "")
        if url:
            return url
    return ""


def _package_name_from_osv(vuln_data: dict[str, Any]) -> str:
    """Extract the package name from the first affected entry."""
    for affected in vuln_data.get("affected", []):
        pkg = affected.get("package", {})
        name = pkg.get("name", "")
        if name:
            return name
    return ""


def _vulns_from_osv(raw_vulns: list[dict[str, Any]], dep: Dependency) -> list[Vulnerability]:
    """Convert raw OSV vuln dicts into Vulnerability objects."""
    results: list[Vulnerability] = []
    for v in raw_vulns:
        vuln = Vulnerability(
            id=v.get("id", ""),
            summary=v.get("summary", ""),
            severity=_severity_from_osv(v),
            package=_package_name_from_osv(v) or dep.name,
            version=dep.version,
            fixed_in=_fixed_version_from_osv(v),
            url=_url_from_osv(v),
            aliases=v.get("aliases", []),
        )
        results.append(vuln)
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def audit_dependencies(path: str) -> AuditReport:
    """Parse dependencies from *path* and check each against OSV.

    Returns an :class:`AuditReport` with all found vulnerabilities.
    """
    deps = parse_dependencies(path)
    all_vulns: list[Vulnerability] = []

    for dep in deps:
        raw = sys.modules[__name__]._query_osv(dep)
        if raw:
            all_vulns.extend(_vulns_from_osv(raw, dep))

    return AuditReport(dependencies=deps, vulnerabilities=all_vulns)


# ---------------------------------------------------------------------------
# DependencyAuditor class (used by language_server.py)
# ---------------------------------------------------------------------------

class DependencyAuditor:
    """Wrapper class for dependency auditing, used by the language server."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    def audit(self, path: str) -> AuditReport:
        """Run a full audit on the given path."""
        return audit_dependencies(path)

    def parse(self, path: str) -> list[Dependency]:
        """Parse dependencies without auditing."""
        return parse_dependencies(path)
