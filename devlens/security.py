"""Security scanner — detects secrets, vulnerabilities, and risky patterns in PR diffs."""

from __future__ import annotations

import os
import re
import json
from dataclasses import dataclass, field
from enum import Enum

from devlens.github import PRData


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class SecurityFinding:
    rule_id: str
    title: str
    severity: Severity
    file: str
    line: int | None = None
    match: str = ""
    description: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "severity": self.severity.value,
            "file": self.file,
            "line": self.line,
            "match": self.match[:80] + "..." if len(self.match) > 80 else self.match,
            "description": self.description,
            "suggestion": self.suggestion,
        }


@dataclass
class ScanResult:
    pr_number: int
    title: str
    total_files: int
    files_scanned: int
    findings: list[SecurityFinding] = field(default_factory=list)
    ai_summary: str = ""

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.HIGH)

    @property
    def medium_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.MEDIUM)

    @property
    def low_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.LOW)

    @property
    def score(self) -> int:
        if not self.findings:
            return 100
        penalty = (
            self.critical_count * 25
            + self.high_count * 15
            + self.medium_count * 5
            + self.low_count * 1
        )
        return max(0, 100 - penalty)

    @property
    def grade(self) -> str:
        s = self.score
        if s >= 90: return "A"
        if s >= 75: return "B"
        if s >= 60: return "C"
        if s >= 40: return "D"
        return "F"

    def to_dict(self) -> dict:
        return {
            "pr_number": self.pr_number,
            "title": self.title,
            "score": self.score,
            "grade": self.grade,
            "total_files": self.total_files,
            "files_scanned": self.files_scanned,
            "summary": {
                "critical": self.critical_count,
                "high": self.high_count,
                "medium": self.medium_count,
                "low": self.low_count,
            },
            "findings": [f.to_dict() for f in self.findings],
            "ai_summary": self.ai_summary,
        }

    def to_markdown(self) -> str:
        lines = [
            f"## Security Scan — PR #{self.pr_number}",
            "",
            f"**Score:** {self.score}/100 (Grade: {self.grade})",
            f"**Files scanned:** {self.files_scanned}/{self.total_files}",
            "",
        ]
        if not self.findings:
            lines.append("No security issues found!")
        else:
            lines.append(f"**Findings:** {self.critical_count} critical, "
                         f"{self.high_count} high, {self.medium_count} medium, "
                         f"{self.low_count} low")
            lines.append("")
            for f in sorted(self.findings, key=lambda x: list(Severity).index(x.severity)):
                sev = f.severity.value.upper()
                lines.append(f"### [{sev}] {f.title}")
                lines.append(f"- **File:** `{f.file}`" + (f" (line {f.line})" if f.line else ""))
                lines.append(f"- **Rule:** `{f.rule_id}`")
                if f.match:
                    display = f.match[:60] + "..." if len(f.match) > 60 else f.match
                    lines.append(f"- **Match:** `{display}`")
                lines.append(f"- {f.description}")
                if f.suggestion:
                    lines.append(f"- **Fix:** {f.suggestion}")
                lines.append("")

        if self.ai_summary:
            lines += ["---", "", "### AI Analysis", "", self.ai_summary]
        return "\n".join(lines)


SECRET_RULES: list[dict] = [
    {
        "id": "SEC001", "title": "AWS Access Key",
        "pattern": r"(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}",
        "severity": Severity.CRITICAL,
        "description": "Hardcoded AWS access key ID detected.",
        "suggestion": "Use environment variables or AWS IAM roles instead.",
    },
    {
        "id": "SEC002", "title": "AWS Secret Key",
        "pattern": r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?",
        "severity": Severity.CRITICAL,
        "description": "Hardcoded AWS secret access key detected.",
        "suggestion": "Rotate this key immediately and use env vars or a secrets manager.",
    },
    {
        "id": "SEC003", "title": "GitHub Token",
        "pattern": r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}",
        "severity": Severity.CRITICAL,
        "description": "GitHub personal access token or OAuth token detected.",
        "suggestion": "Revoke this token and use GITHUB_TOKEN env var or GitHub App auth.",
    },
    {
        "id": "SEC004", "title": "Generic API Key Assignment",
        "pattern": r"(?i)(?:api[_\-]?key|apikey|secret[_\-]?key|access[_\-]?token|auth[_\-]?token)\s*[=:]\s*['\"][A-Za-z0-9\-_./+=]{16,}['\"]",
        "severity": Severity.HIGH,
        "description": "Hardcoded API key or token in source code.",
        "suggestion": "Move secrets to environment variables or a vault.",
    },
    {
        "id": "SEC005", "title": "Private Key Block",
        "pattern": r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
        "severity": Severity.CRITICAL,
        "description": "Private key embedded in source code.",
        "suggestion": "Remove immediately. Store keys in a secure vault, never in code.",
    },
    {
        "id": "SEC006", "title": "JWT Token",
        "pattern": r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
        "severity": Severity.HIGH,
        "description": "Hardcoded JWT token detected.",
        "suggestion": "Remove the token and generate dynamically at runtime.",
    },
    {
        "id": "SEC007", "title": "Slack Token",
        "pattern": r"xox[bporas]-[0-9]{10,}-[A-Za-z0-9-]+",
        "severity": Severity.CRITICAL,
        "description": "Slack API token detected.",
        "suggestion": "Revoke and regenerate. Use environment variables.",
    },
    {
        "id": "SEC008", "title": "Database Connection String",
        "pattern": r"(?i)(?:postgres|mysql|mongodb|redis|mssql)://[^\s'\"]{10,}",
        "severity": Severity.HIGH,
        "description": "Database connection string with potential credentials.",
        "suggestion": "Use DATABASE_URL env var or a secrets manager.",
    },
    {
        "id": "SEC009", "title": "Hardcoded Password",
        "pattern": r"(?i)(?:password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{6,}['\"]",
        "severity": Severity.HIGH,
        "description": "Hardcoded password in source code.",
        "suggestion": "Never hardcode passwords. Use env vars or a vault.",
    },
    {
        "id": "SEC010", "title": "Google API Key",
        "pattern": r"AIza[0-9A-Za-z\-_]{35}",
        "severity": Severity.HIGH,
        "description": "Google API key detected.",
        "suggestion": "Restrict API key scope and move to env vars.",
    },
    {
        "id": "SEC011", "title": "Stripe Key",
        "pattern": r"(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{20,}",
        "severity": Severity.CRITICAL,
        "description": "Stripe API key detected.",
        "suggestion": "Revoke and regenerate. Use STRIPE_SECRET_KEY env var.",
    },
    {
        "id": "SEC012", "title": "SendGrid Key",
        "pattern": r"SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}",
        "severity": Severity.CRITICAL,
        "description": "SendGrid API key detected.",
        "suggestion": "Rotate immediately. Use environment variables.",
    },
]

VULN_RULES: list[dict] = [
    {
        "id": "VLN001", "title": "SQL Injection Risk",
        "pattern": r"(?i)(?:execute|cursor\.execute|raw|rawsql)\s*\(\s*(?:f['\"]|['\"].*%s|.*\.format\()",
        "severity": Severity.HIGH,
        "description": "Potential SQL injection — user input may be interpolated into a query.",
        "suggestion": "Use parameterized queries (placeholders) instead of string formatting.",
    },
    {
        "id": "VLN002", "title": "Command Injection Risk",
        "pattern": r"(?i)(?:os\.system|os\.popen|subprocess\.call|subprocess\.run|subprocess\.Popen)\s*\(\s*(?:f['\"]|.*\.format\(|.*%\s)",
        "severity": Severity.HIGH,
        "description": "Potential command injection — user input may reach a shell command.",
        "suggestion": "Use subprocess with a list of args (shell=False) and validate inputs.",
    },
    {
        "id": "VLN003", "title": "Unsafe Deserialization",
        "pattern": r"(?i)(?:pickle\.loads?|yaml\.load\s*\([^)]*(?!Loader))",
        "severity": Severity.HIGH,
        "description": "Unsafe deserialization can lead to remote code execution.",
        "suggestion": "Use yaml.safe_load() or avoid pickle for untrusted data.",
    },
    {
        "id": "VLN004", "title": "Eval / Exec Usage",
        "pattern": r"(?<!\w)(?:eval|exec)\s*\(",
        "severity": Severity.MEDIUM,
        "description": "eval() or exec() can execute arbitrary code if input is untrusted.",
        "suggestion": "Use ast.literal_eval() for data parsing or avoid eval entirely.",
    },
    {
        "id": "VLN005", "title": "Disabled SSL Verification",
        "pattern": r"(?i)verify\s*=\s*False",
        "severity": Severity.MEDIUM,
        "description": "SSL certificate verification is disabled, enabling MITM attacks.",
        "suggestion": "Enable SSL verification (verify=True) or use proper CA bundles.",
    },
    {
        "id": "VLN006", "title": "Insecure Hash Algorithm",
        "pattern": r"(?i)(?:hashlib\.(?:md5|sha1)|\.(?:md5|sha1)\s*\()",
        "severity": Severity.MEDIUM,
        "description": "MD5/SHA1 are cryptographically broken for security purposes.",
        "suggestion": "Use SHA-256 or stronger (hashlib.sha256) for security-sensitive hashing.",
    },
    {
        "id": "VLN007", "title": "Hardcoded IP / Localhost Binding",
        "pattern": r"(?:0\.0\.0\.0|127\.0\.0\.1):\d{2,5}",
        "severity": Severity.LOW,
        "description": "Service binding to all interfaces (0.0.0.0) or localhost with a port.",
        "suggestion": "Bind to specific interfaces in production. Use env vars for host/port.",
    },
    {
        "id": "VLN008", "title": "Debug Mode Enabled",
        "pattern": r"(?i)(?:debug\s*=\s*True|DEBUG\s*=\s*True|app\.run\(.*debug\s*=\s*True)",
        "severity": Severity.MEDIUM,
        "description": "Debug mode should not be enabled in production.",
        "suggestion": "Use environment-based config: DEBUG = os.getenv('DEBUG', 'false').",
    },
    {
        "id": "VLN009", "title": "CORS Allow All Origins",
        "pattern": r"(?i)(?:access-control-allow-origin['\": ]*\*|allow_origins\s*=\s*\[?\s*['\"]\*['\"])",
        "severity": Severity.MEDIUM,
        "description": "CORS configured to allow all origins — may expose API to unauthorized sites.",
        "suggestion": "Restrict CORS to specific trusted domains.",
    },
    {
        "id": "VLN010", "title": "Insecure Randomness",
        "pattern": r"(?<!\w)random\.(?:random|randint|choice|randrange)\s*\(",
        "severity": Severity.LOW,
        "description": "Python's random module is not cryptographically secure.",
        "suggestion": "Use secrets module (secrets.token_hex, secrets.randbelow) for security purposes.",
    },
    {
        "id": "VLN011", "title": "XSS Risk",
        "pattern": r"(?:innerHTML|dangerouslySetInnerHTML|v-html)\s*[=:]",
        "severity": Severity.MEDIUM,
        "description": "Direct HTML injection can lead to Cross-Site Scripting (XSS).",
        "suggestion": "Sanitize user input with DOMPurify or use textContent instead.",
    },
    {
        "id": "VLN012", "title": "Prototype Pollution",
        "pattern": r"(?:__proto__|constructor\s*\[|Object\.assign\s*\(\s*\{\s*\})",
        "severity": Severity.MEDIUM,
        "description": "Potential prototype pollution vulnerability in JavaScript.",
        "suggestion": "Validate and sanitize object keys. Use Object.create(null) for dictionaries.",
    },
]

RISKY_FILES: list[dict] = [
    {
        "id": "FIL001",
        "pattern": r"(?i)\.env(?:\.local|\.prod|\.staging)?$",
        "title": "Environment File Committed",
        "severity": Severity.HIGH,
        "description": ".env files often contain secrets and should not be committed.",
        "suggestion": "Add .env* to .gitignore and use a secrets manager.",
    },
    {
        "id": "FIL002",
        "pattern": r"(?i)(?:id_rsa|id_ed25519|id_ecdsa)(?:\.pub)?$",
        "title": "SSH Key File",
        "severity": Severity.CRITICAL,
        "description": "SSH key file detected in the repository.",
        "suggestion": "Remove immediately. Never commit SSH keys.",
    },
    {
        "id": "FIL003",
        "pattern": r"(?i)\.(?:pem|key|p12|pfx|jks|keystore)$",
        "title": "Certificate / Key File",
        "severity": Severity.HIGH,
        "description": "Certificate or key file detected.",
        "suggestion": "Store certificates in a vault, not in source control.",
    },
    {
        "id": "FIL004",
        "pattern": r"(?i)(?:credentials|secrets|passwords)\.(?:json|yml|yaml|xml|txt)$",
        "title": "Credentials File",
        "severity": Severity.HIGH,
        "description": "File name suggests it contains credentials.",
        "suggestion": "Use a secrets manager and add to .gitignore.",
    },
]

DEFAULT_IGNORE = [
    r"\.lock$", r"package-lock\.json$", r"yarn\.lock$", r"go\.sum$",
    r"\.min\.(js|css)$", r"dist/", r"node_modules/", r"vendor/",
    r"__pycache__/", r"\.generated\.",
]


def _should_skip(filename: str, ignore_patterns: list[str] | None = None) -> bool:
    patterns = ignore_patterns or DEFAULT_IGNORE
    return any(re.search(p, filename) for p in patterns)


def _extract_added_lines(patch: str) -> list[tuple[int, str]]:
    if not patch:
        return []
    results = []
    current_line = 0
    for line in patch.split("\n"):
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            if m:
                current_line = int(m.group(1)) - 1
        elif line.startswith("+") and not line.startswith("+++"):
            current_line += 1
            results.append((current_line, line[1:]))
        elif line.startswith("-"):
            pass
        else:
            current_line += 1
    return results


def scan_pr(
    pr: PRData,
    *,
    use_ai: bool = False,
    model: str = "gpt-4o",
    ignore_patterns: list[str] | None = None,
    custom_rules: list[dict] | None = None,
) -> ScanResult:
    findings: list[SecurityFinding] = []
    files_scanned = 0
    all_secret_rules = SECRET_RULES + (custom_rules or [])

    for f in pr.files:
        filename = f["filename"]
        if _should_skip(filename, ignore_patterns):
            continue

        for rule in RISKY_FILES:
            if re.search(rule["pattern"], filename):
                findings.append(SecurityFinding(
                    rule_id=rule["id"], title=rule["title"],
                    severity=rule["severity"], file=filename,
                    description=rule["description"], suggestion=rule["suggestion"],
                ))

        patch = f.get("patch", "")
        if not patch:
            continue
        files_scanned += 1
        added_lines = _extract_added_lines(patch)

        for line_no, content in added_lines:
            for rule in all_secret_rules:
                if re.search(rule["pattern"], content):
                    findings.append(SecurityFinding(
                        rule_id=rule["id"], title=rule["title"],
                        severity=rule["severity"], file=filename,
                        line=line_no, match=content.strip(),
                        description=rule["description"], suggestion=rule["suggestion"],
                    ))
            for rule in VULN_RULES:
                if re.search(rule["pattern"], content):
                    findings.append(SecurityFinding(
                        rule_id=rule["id"], title=rule["title"],
                        severity=rule["severity"], file=filename,
                        line=line_no, match=content.strip(),
                        description=rule["description"], suggestion=rule["suggestion"],
                    ))

    ai_summary = ""
    if use_ai and pr.files:
        ai_summary = _ai_security_review(pr, findings, model)

    return ScanResult(
        pr_number=pr.number, title=pr.title,
        total_files=pr.changed_files, files_scanned=files_scanned,
        findings=findings, ai_summary=ai_summary,
    )


SECURITY_PROMPT = """You are an expert application security engineer performing a code review.

Analyze the following Pull Request diff for security vulnerabilities.

The static scanner already found these issues:
{static_findings}

Now look DEEPER for issues the regex scanner might miss:
1. Logic flaws (auth bypass, race conditions, TOCTOU)
2. Business logic vulnerabilities
3. Insufficient input validation
4. Missing authorization checks
5. Information disclosure (stack traces, verbose errors)
6. Insecure defaults or configurations
7. Dependency-related risks

PR Title: {title}
PR Description: {body}

Diff:
{diff}

Return a JSON object:
{{
  "additional_findings": [
    {{"title": "...", "severity": "critical|high|medium|low", "file": "filename", "description": "...", "suggestion": "..."}}
  ],
  "overall_assessment": "1-2 paragraph security assessment of this PR"
}}

Only report REAL issues visible in the diff. Do not invent hypothetical problems."""


def _ai_security_review(pr: PRData, static_findings: list[SecurityFinding], model: str) -> str:
    diff_parts = []
    for f in pr.files:
        patch = f.get("patch", "")
        if patch:
            diff_parts.append(f"--- {f['filename']} ---\n{patch}")

    diff_text = "\n\n".join(diff_parts)
    if len(diff_text) > 30000:
        diff_text = diff_text[:30000] + "\n\n... (truncated)"

    static_summary = "None" if not static_findings else "\n".join(
        f"- [{f.severity.value.upper()}] {f.title} in {f.file}" for f in static_findings
    )

    prompt = SECURITY_PROMPT.format(
        static_findings=static_summary,
        title=pr.title,
        body=pr.body[:2000] if pr.body else "(no description)",
        diff=diff_text,
    )

    try:
        from devlens.analyzer import _call_llm
        raw = _call_llm(model, prompt)
        data = json.loads(raw)
        return data.get("overall_assessment", "")
    except Exception:
        return ""


def scan_path(path: str, *, ignore_patterns: list[str] | None = None) -> list[SecurityFinding]:
    import pathlib
    root = pathlib.Path(path)
    findings: list[SecurityFinding] = []
    patterns = ignore_patterns or DEFAULT_IGNORE

    for fp in root.rglob("*"):
        if not fp.is_file():
            continue
        rel = str(fp.relative_to(root))
        if any(re.search(p, rel) for p in patterns):
            continue

        for rule in RISKY_FILES:
            if re.search(rule["pattern"], rel):
                findings.append(SecurityFinding(
                    rule_id=rule["id"], title=rule["title"],
                    severity=rule["severity"], file=rel,
                    description=rule["description"], suggestion=rule["suggestion"],
                ))

        if fp.stat().st_size > 500_000:
            continue
        try:
            content = fp.read_text(errors="ignore")
        except Exception:
            continue

        for line_no, line in enumerate(content.splitlines(), 1):
            for rule in SECRET_RULES:
                if re.search(rule["pattern"], line):
                    findings.append(SecurityFinding(
                        rule_id=rule["id"], title=rule["title"],
                        severity=rule["severity"], file=rel,
                        line=line_no, match=line.strip(),
                        description=rule["description"], suggestion=rule["suggestion"],
                    ))
            for rule in VULN_RULES:
                if re.search(rule["pattern"], line):
                    findings.append(SecurityFinding(
                        rule_id=rule["id"], title=rule["title"],
                        severity=rule["severity"], file=rel,
                        line=line_no, match=line.strip(),
                        description=rule["description"], suggestion=rule["suggestion"],
                    ))

    return findings
