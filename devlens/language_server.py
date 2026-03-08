"""DevLens Language Server — pygls 2.0 based LSP implementation.

Provides real-time code analysis diagnostics, code actions (auto-fix),
hover information, and CodeLens quality scores for IDE integration.

Usage:
    # STDIO mode (VS Code extension default)
    python -m devlens.language_server

    # TCP mode (debugging)
    python -m devlens.language_server --mode tcp --port 2087

Requires: pygls>=2.0, lsprotocol>=2025.0
    pip install devlens[ide]
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

try:
    from lsprotocol import types as lsp
    from pygls.lsp.server import LanguageServer
    from pygls.workspace import TextDocument
except ImportError:
    print(
        "DevLens LSP requires pygls and lsprotocol.\n"
        "Install with: pip install devlens[ide]",
        file=sys.stderr,
    )
    sys.exit(1)

from devlens.cache import AnalysisCache
from devlens.complexity import ComplexityAnalyzer
from devlens.config import load_config, get_lsp_config
from devlens.depaudit import DependencyAuditor
from devlens.rules import RulesEngine
from devlens.scoreboard import ScoreCalculator

try:
    from devlens.ai_review import AIReviewer
    HAS_AI = True
except ImportError:
    HAS_AI = False

try:
    from devlens.fixer import AutoFixer
    HAS_FIXER = True
except ImportError:
    HAS_FIXER = False

logger = logging.getLogger("devlens.lsp")

__all__ = ["DevLensLanguageServer", "start_server"]

# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------

SEVERITY_MAP: dict[str, lsp.DiagnosticSeverity] = {
    "critical": lsp.DiagnosticSeverity.Error,
    "high": lsp.DiagnosticSeverity.Warning,
    "medium": lsp.DiagnosticSeverity.Information,
    "low": lsp.DiagnosticSeverity.Hint,
    "info": lsp.DiagnosticSeverity.Hint,
}

DIAGNOSTIC_SOURCE = "devlens"

DIAGNOSTIC_TAGS: dict[str, list[lsp.DiagnosticTag]] = {
    "deprecated": [lsp.DiagnosticTag.Deprecated],
    "unused": [lsp.DiagnosticTag.Unnecessary],
}


# ---------------------------------------------------------------------------
# Analysis result -> LSP diagnostic conversion
# ---------------------------------------------------------------------------

def _finding_to_diagnostic(
    finding: dict[str, Any],
    category: str,
) -> lsp.Diagnostic:
    """Convert a DevLens finding dict to an LSP Diagnostic."""
    line = max(0, finding.get("line", 1) - 1)
    col = max(0, finding.get("column", 0))
    end_line = max(line, finding.get("end_line", line + 1) - 1)
    end_col = finding.get("end_column", col + 1)

    severity_str = finding.get("severity", "medium").lower()
    severity = SEVERITY_MAP.get(severity_str, lsp.DiagnosticSeverity.Information)

    message = finding.get("message", finding.get("description", "Issue detected"))
    rule_id = finding.get("rule_id", finding.get("id", f"{category}-issue"))
    code = f"devlens/{category}/{rule_id}"

    tags: list[lsp.DiagnosticTag] = []
    for tag_key, tag_values in DIAGNOSTIC_TAGS.items():
        if tag_key in message.lower() or tag_key in rule_id.lower():
            tags.extend(tag_values)

    return lsp.Diagnostic(
        range=lsp.Range(
            start=lsp.Position(line=line, character=col),
            end=lsp.Position(line=end_line, character=end_col),
        ),
        severity=severity,
        code=code,
        source=DIAGNOSTIC_SOURCE,
        message=message,
        tags=tags if tags else None,
        data={
            "category": category,
            "rule_id": rule_id,
            "finding": finding,
        },
    )


def _score_to_grade(score: float) -> str:
    """Convert numeric score (0-100) to letter grade."""
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    else:
        return "F"


# ---------------------------------------------------------------------------
# DevLens Language Server
# ---------------------------------------------------------------------------

class DevLensLanguageServer(LanguageServer):
    """Language Server that provides DevLens code analysis features.

    Features:
        - Diagnostics: security, complexity, rules, dependency findings
        - Code Actions: auto-fix suggestions as quick fixes
        - Hover: rule explanations and suggestions
        - CodeLens: per-file quality score (A-F grading)
        - Commands: analyzeFile, showDashboard, configureAI
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        self._config: dict[str, Any] = {}
        self._lsp_config: dict[str, Any] = {}

        self._rules_engine: Optional[RulesEngine] = None
        self._complexity_analyzer: Optional[ComplexityAnalyzer] = None
        self._dep_auditor: Optional[DependencyAuditor] = None
        self._score_calculator: Optional[ScoreCalculator] = None
        self._ai_reviewer: Optional[Any] = None
        self._auto_fixer: Optional[Any] = None
        self._cache: Optional[AnalysisCache] = None

        self._file_scores: dict[str, float] = {}
        self._file_findings: dict[str, list[dict]] = {}
        self._debounce_tasks: dict[str, asyncio.Task] = {}
        self._analysis_lock = asyncio.Lock()

        self._register_handlers()

    def _init_analyzers(self) -> None:
        """Lazy-initialize analyzers on first use."""
        if self._rules_engine is not None:
            return

        try:
            self._config = load_config()
            self._lsp_config = get_lsp_config(self._config)
        except Exception as e:
            logger.warning("Failed to load config, using defaults: %s", e)
            self._config = {}
            self._lsp_config = {}

        self._rules_engine = RulesEngine(self._config)
        self._complexity_analyzer = ComplexityAnalyzer(self._config)
        self._dep_auditor = DependencyAuditor(self._config)
        self._score_calculator = ScoreCalculator(self._config)
        self._cache = AnalysisCache(self._config)

        if HAS_FIXER:
            try:
                self._auto_fixer = AutoFixer(self._config)
            except Exception as e:
                logger.warning("AutoFixer init failed: %s", e)

        if HAS_AI and self._config.get("ai_review", {}).get("enabled", False):
            try:
                self._ai_reviewer = AIReviewer(self._config)
            except Exception as e:
                logger.warning("AIReviewer init failed: %s", e)

        logger.info("DevLens analyzers initialized")

    def _register_handlers(self) -> None:
        """Register all LSP event handlers."""

        @self.feature(lsp.INITIALIZE)
        def on_initialize(params: lsp.InitializeParams) -> None:
            logger.info("DevLens LSP initializing for workspace: %s", params.root_uri)
            self._init_analyzers()

        @self.feature(lsp.INITIALIZED)
        def on_initialized(params: lsp.InitializedParams) -> None:
            logger.info("DevLens LSP initialized and ready")
            self.register_capability(
                lsp.RegistrationParams(
                    registrations=[
                        lsp.Registration(
                            id="devlens-config",
                            method="workspace/didChangeConfiguration",
                        )
                    ]
                )
            )

        @self.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
        async def on_did_open(params: lsp.DidOpenTextDocumentParams) -> None:
            uri = params.text_document.uri
            if self._should_analyze(uri):
                if self._lsp_config.get("lint_on_open", True):
                    await self._run_analysis(uri)

        @self.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
        async def on_did_save(params: lsp.DidSaveTextDocumentParams) -> None:
            uri = params.text_document.uri
            if self._should_analyze(uri):
                if self._lsp_config.get("lint_on_save", True):
                    await self._run_analysis(uri)

        @self.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
        async def on_did_change(params: lsp.DidChangeTextDocumentParams) -> None:
            uri = params.text_document.uri
            if not self._should_analyze(uri):
                return
            if not self._lsp_config.get("lint_on_change", False):
                return
            debounce_ms = self._lsp_config.get("debounce_ms", 1000)
            await self._debounced_analysis(uri, debounce_ms)

        @self.feature(lsp.TEXT_DOCUMENT_DID_CLOSE)
        def on_did_close(params: lsp.DidCloseTextDocumentParams) -> None:
            uri = params.text_document.uri
            self.publish_diagnostics(uri, [])
            self._file_scores.pop(uri, None)
            self._file_findings.pop(uri, None)
            task = self._debounce_tasks.pop(uri, None)
            if task and not task.done():
                task.cancel()

        @self.feature(lsp.WORKSPACE_DID_CHANGE_CONFIGURATION)
        def on_config_change(params: lsp.DidChangeConfigurationParams) -> None:
            settings = params.settings or {}
            devlens_settings = settings.get("devlens", {})
            if devlens_settings:
                self._apply_settings(devlens_settings)
                logger.info("Configuration updated from IDE")

        @self.feature(lsp.TEXT_DOCUMENT_CODE_ACTION)
        async def on_code_action(
            params: lsp.CodeActionParams,
        ) -> list[lsp.CodeAction]:
            return await self._get_code_actions(params)

        @self.feature(lsp.TEXT_DOCUMENT_HOVER)
        async def on_hover(
            params: lsp.HoverParams,
        ) -> Optional[lsp.Hover]:
            return await self._get_hover(params)

        @self.feature(lsp.TEXT_DOCUMENT_CODE_LENS)
        async def on_code_lens(
            params: lsp.CodeLensParams,
        ) -> list[lsp.CodeLens]:
            return await self._get_code_lens(params)

        @self.feature(lsp.WORKSPACE_EXECUTE_COMMAND)
        async def on_execute_command(
            params: lsp.ExecuteCommandParams,
        ) -> Any:
            return await self._execute_command(params)

        @self.feature(lsp.SHUTDOWN)
        def on_shutdown(params: Any) -> None:
            logger.info("DevLens LSP shutting down")
            if self._cache:
                self._cache.close()

    # -------------------------------------------------------------------
    # Analysis pipeline
    # -------------------------------------------------------------------

    def _should_analyze(self, uri: str) -> bool:
        """Check if a file should be analyzed."""
        if not uri.startswith("file://"):
            return False
        path = uri.replace("file://", "")
        supported = (".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".rb")
        return any(path.endswith(ext) for ext in supported)

    async def _debounced_analysis(self, uri: str, delay_ms: int) -> None:
        """Run analysis with debouncing for on-change events."""
        existing = self._debounce_tasks.get(uri)
        if existing and not existing.done():
            existing.cancel()

        async def _delayed() -> None:
            await asyncio.sleep(delay_ms / 1000.0)
            await self._run_analysis(uri)

        self._debounce_tasks[uri] = asyncio.create_task(_delayed())

    async def _run_analysis(self, uri: str) -> None:
        """Run full DevLens analysis on a file and publish diagnostics."""
        async with self._analysis_lock:
            start_time = time.monotonic()
            file_path = uri.replace("file://", "")
            path = Path(file_path)

            if not path.exists():
                logger.warning("File not found: %s", file_path)
                return

            self._init_analyzers()

            try:
                if self._cache:
                    cached = self._cache.get(file_path)
                    if cached:
                        logger.debug("Cache hit for %s", path.name)
                        self._publish_cached_results(uri, cached)
                        return

                source_code = path.read_text(encoding="utf-8", errors="replace")
                diagnostics: list[lsp.Diagnostic] = []
                all_findings: list[dict] = []

                # 1. Rules engine analysis
                if self._rules_engine:
                    try:
                        rules_result = self._rules_engine.analyze(file_path, source_code)
                        for finding in rules_result.get("findings", []):
                            diag = _finding_to_diagnostic(finding, "rules")
                            diagnostics.append(diag)
                            all_findings.append({**finding, "_category": "rules"})
                    except Exception as e:
                        logger.error("Rules analysis failed for %s: %s", path.name, e)

                # 2. Complexity analysis
                if self._complexity_analyzer:
                    try:
                        complexity_result = self._complexity_analyzer.analyze(
                            file_path, source_code
                        )
                        for finding in complexity_result.get("findings", []):
                            diag = _finding_to_diagnostic(finding, "complexity")
                            diagnostics.append(diag)
                            all_findings.append({**finding, "_category": "complexity"})
                    except Exception as e:
                        logger.error("Complexity analysis failed for %s: %s", path.name, e)

                # 3. Security analysis
                if self._rules_engine:
                    try:
                        security_result = self._rules_engine.analyze_security(
                            file_path, source_code
                        )
                        for finding in security_result.get("findings", []):
                            diag = _finding_to_diagnostic(finding, "security")
                            diagnostics.append(diag)
                            all_findings.append({**finding, "_category": "security"})
                    except Exception as e:
                        logger.error("Security analysis failed for %s: %s", path.name, e)

                # 4. Dependency audit
                dep_files = (
                    "requirements.txt", "Pipfile", "pyproject.toml",
                    "package.json", "Gemfile", "go.mod", "Cargo.toml",
                )
                if path.name in dep_files and self._dep_auditor:
                    try:
                        dep_result = self._dep_auditor.audit(file_path)
                        for finding in dep_result.get("findings", []):
                            diag = _finding_to_diagnostic(finding, "dependency")
                            diagnostics.append(diag)
                            all_findings.append({**finding, "_category": "dependency"})
                    except Exception as e:
                        logger.error("Dependency audit failed for %s: %s", path.name, e)

                # 5. Calculate file score
                score = 100.0
                if self._score_calculator:
                    try:
                        score = self._score_calculator.calculate_file_score(
                            all_findings, source_code
                        )
                    except Exception as e:
                        logger.error("Score calc failed: %s", e)

                self._file_scores[uri] = score
                self._file_findings[uri] = all_findings

                if self._cache:
                    self._cache.set(file_path, {
                        "findings": all_findings,
                        "score": score,
                        "diagnostics_count": len(diagnostics),
                    })

                self.publish_diagnostics(uri, diagnostics)

                elapsed = (time.monotonic() - start_time) * 1000
                grade = _score_to_grade(score)
                logger.info(
                    "Analysis complete: %s — %d issues, score %.1f (%s), %.0fms",
                    path.name,
                    len(diagnostics),
                    score,
                    grade,
                    elapsed,
                )

                self._notify_score(uri, score, grade, len(diagnostics))

            except Exception as e:
                logger.error("Analysis failed for %s: %s", file_path, e)
                self.show_message(
                    f"DevLens analysis failed: {e}",
                    lsp.MessageType.Error,
                )

    def _publish_cached_results(self, uri: str, cached: dict) -> None:
        """Publish diagnostics from cached analysis results."""
        diagnostics = []
        for finding in cached.get("findings", []):
            category = finding.get("_category", "rules")
            diag = _finding_to_diagnostic(finding, category)
            diagnostics.append(diag)

        self._file_scores[uri] = cached.get("score", 100.0)
        self._file_findings[uri] = cached.get("findings", [])
        self.publish_diagnostics(uri, diagnostics)

    def _notify_score(self, uri: str, score: float, grade: str, issue_count: int) -> None:
        """Send score notification to the client."""
        try:
            self.send_notification(
                "devlens/analysisComplete",
                {
                    "uri": uri,
                    "score": score,
                    "grade": grade,
                    "issueCount": issue_count,
                },
            )
        except Exception:
            pass

    def _apply_settings(self, settings: dict[str, Any]) -> None:
        """Apply IDE settings to LSP configuration."""
        mapping = {
            "lintOnSave": "lint_on_save",
            "lintOnChange": "lint_on_change",
            "lintOnOpen": "lint_on_open",
            "debounceMs": "debounce_ms",
            "logLevel": "log_level",
        }
        for ide_key, config_key in mapping.items():
            if ide_key in settings:
                self._lsp_config[config_key] = settings[ide_key]

        log_level = settings.get("logLevel", "info").upper()
        logging.getLogger("devlens").setLevel(getattr(logging, log_level, logging.INFO))

        ai_settings = settings.get("aiReview", {})
        if isinstance(ai_settings, dict) and "enabled" in ai_settings:
            if ai_settings["enabled"] and not self._ai_reviewer and HAS_AI:
                try:
                    self._ai_reviewer = AIReviewer(self._config)
                except Exception as e:
                    logger.warning("Failed to enable AI reviewer: %s", e)
            elif not ai_settings["enabled"]:
                self._ai_reviewer = None

    # -------------------------------------------------------------------
    # Code Actions (quick fixes)
    # -------------------------------------------------------------------

    async def _get_code_actions(
        self,
        params: lsp.CodeActionParams,
    ) -> list[lsp.CodeAction]:
        """Generate code actions (quick fixes) for diagnostics."""
        actions: list[lsp.CodeAction] = []
        uri = params.text_document.uri

        if not HAS_FIXER or not self._auto_fixer:
            return actions

        for diagnostic in params.context.diagnostics:
            if diagnostic.source != DIAGNOSTIC_SOURCE:
                continue

            data = diagnostic.data or {}
            finding = data.get("finding", {})
            category = data.get("category", "")

            try:
                fix = self._auto_fixer.suggest_fix(finding, category)
                if not fix:
                    continue

                edit = lsp.TextEdit(
                    range=diagnostic.range,
                    new_text=fix.get("replacement", ""),
                )

                workspace_edit = lsp.WorkspaceEdit(
                    changes={uri: [edit]}
                )

                action = lsp.CodeAction(
                    title=fix.get("title", f"Fix: {diagnostic.message[:50]}"),
                    kind=lsp.CodeActionKind.QuickFix,
                    diagnostics=[diagnostic],
                    edit=workspace_edit,
                    is_preferred=fix.get("preferred", False),
                )
                actions.append(action)

                if fix.get("explanation"):
                    explain_action = lsp.CodeAction(
                        title=f"Explain: {diagnostic.code}",
                        kind=lsp.CodeActionKind.Empty,
                        diagnostics=[diagnostic],
                        command=lsp.Command(
                            title="Show Explanation",
                            command="devlens.showExplanation",
                            arguments=[{
                                "rule": diagnostic.code,
                                "explanation": fix["explanation"],
                            }],
                        ),
                    )
                    actions.append(explain_action)

            except Exception as e:
                logger.debug("Fix suggestion failed for %s: %s", diagnostic.code, e)

        return actions

    # -------------------------------------------------------------------
    # Hover (rule explanations)
    # -------------------------------------------------------------------

    async def _get_hover(
        self,
        params: lsp.HoverParams,
    ) -> Optional[lsp.Hover]:
        """Show rule explanation and suggestions on hover over diagnostics."""
        uri = params.text_document.uri
        position = params.position
        findings = self._file_findings.get(uri, [])

        if not findings:
            return None

        for finding in findings:
            line = max(0, finding.get("line", 1) - 1)
            end_line = max(line, finding.get("end_line", line + 1) - 1)

            if line <= position.line <= end_line:
                category = finding.get("_category", "unknown")
                rule_id = finding.get("rule_id", finding.get("id", "unknown"))
                message = finding.get("message", "")
                severity = finding.get("severity", "medium")
                suggestion = finding.get("suggestion", finding.get("fix", ""))

                lines = [
                    f"### DevLens: {category.title()} — `{rule_id}`",
                    "",
                    f"**Severity:** {severity.upper()}",
                    "",
                    message,
                ]

                if suggestion:
                    lines.extend([
                        "",
                        "---",
                        "",
                        f"**Suggestion:** {suggestion}",
                    ])

                ref_url = finding.get("reference", finding.get("url", ""))
                if ref_url:
                    lines.extend(["", f"[Learn more]({ref_url})"])

                return lsp.Hover(
                    contents=lsp.MarkupContent(
                        kind=lsp.MarkupKind.Markdown,
                        value="\n".join(lines),
                    ),
                    range=lsp.Range(
                        start=lsp.Position(line=line, character=0),
                        end=lsp.Position(line=end_line, character=0),
                    ),
                )

        return None

    # -------------------------------------------------------------------
    # CodeLens (file quality score)
    # -------------------------------------------------------------------

    async def _get_code_lens(
        self,
        params: lsp.CodeLensParams,
    ) -> list[lsp.CodeLens]:
        """Show quality score as CodeLens at top of file."""
        uri = params.text_document.uri
        score = self._file_scores.get(uri)
        lenses: list[lsp.CodeLens] = []

        if score is not None:
            grade = _score_to_grade(score)
            findings = self._file_findings.get(uri, [])
            issue_count = len(findings)

            title = f"DevLens: {grade} ({score:.0f}/100) — {issue_count} issue{'s' if issue_count != 1 else ''}"

            lens = lsp.CodeLens(
                range=lsp.Range(
                    start=lsp.Position(line=0, character=0),
                    end=lsp.Position(line=0, character=0),
                ),
                command=lsp.Command(
                    title=title,
                    command="devlens.showDashboard",
                    arguments=[uri],
                ),
            )
            lenses.append(lens)

        return lenses

    # -------------------------------------------------------------------
    # Commands
    # -------------------------------------------------------------------

    async def _execute_command(
        self,
        params: lsp.ExecuteCommandParams,
    ) -> Any:
        """Handle custom DevLens commands."""
        command = params.command
        args = params.arguments or []

        if command == "devlens.analyzeFile":
            if args:
                uri = args[0]
                await self._run_analysis(uri)
                return {"status": "ok", "uri": uri}

        elif command == "devlens.showDashboard":
            uri = args[0] if args else None
            score = self._file_scores.get(uri, 0) if uri else 0
            grade = _score_to_grade(score)
            findings = self._file_findings.get(uri, []) if uri else []

            by_category: dict[str, list] = {}
            for f in findings:
                cat = f.get("_category", "other")
                by_category.setdefault(cat, []).append(f)

            summary_lines = [
                f"# DevLens Dashboard — {grade} ({score:.0f}/100)",
                "",
            ]
            for cat, cat_findings in sorted(by_category.items()):
                summary_lines.append(f"## {cat.title()} ({len(cat_findings)} issues)")
                for f in cat_findings[:5]:
                    msg = f.get("message", "")[:80]
                    sev = f.get("severity", "?").upper()
                    summary_lines.append(f"  - [{sev}] {msg}")
                if len(cat_findings) > 5:
                    summary_lines.append(f"  ... and {len(cat_findings) - 5} more")
                summary_lines.append("")

            self.show_message(
                f"DevLens: {grade} ({score:.0f}/100) - {len(findings)} issues found",
                lsp.MessageType.Info,
            )

            return {
                "score": score,
                "grade": grade,
                "issueCount": len(findings),
                "summary": "\n".join(summary_lines),
            }

        elif command == "devlens.showExplanation":
            if args:
                data = args[0] if isinstance(args[0], dict) else {}
                rule = data.get("rule", "Unknown")
                explanation = data.get("explanation", "No explanation available.")
                self.show_message(
                    f"{rule}: {explanation}",
                    lsp.MessageType.Info,
                )
                return {"rule": rule, "explanation": explanation}

        elif command == "devlens.configureAI":
            if HAS_AI:
                self.show_message(
                    "AI Review is available. Configure provider and API key "
                    "in .devlens.toml or IDE settings.",
                    lsp.MessageType.Info,
                )
            else:
                self.show_message(
                    "AI Review requires extra dependencies. "
                    "Install with: pip install devlens[ai]",
                    lsp.MessageType.Warning,
                )
            return {"ai_available": HAS_AI}

        elif command == "devlens.clearCache":
            if self._cache:
                self._cache.clear()
                self.show_message(
                    "DevLens cache cleared.",
                    lsp.MessageType.Info,
                )
            return {"status": "ok"}

        elif command == "devlens.analyzeWorkspace":
            analyzed = 0
            for doc_uri in list(self.workspace.text_documents.keys()):
                if self._should_analyze(doc_uri):
                    await self._run_analysis(doc_uri)
                    analyzed += 1
            self.show_message(
                f"DevLens: Analyzed {analyzed} files in workspace.",
                lsp.MessageType.Info,
            )
            return {"status": "ok", "filesAnalyzed": analyzed}

        return None


# ---------------------------------------------------------------------------
# Server initialization capabilities
# ---------------------------------------------------------------------------

def _build_server_capabilities() -> lsp.ServerCapabilities:
    """Build server capabilities for the InitializeResult."""
    return lsp.ServerCapabilities(
        text_document_sync=lsp.TextDocumentSyncOptions(
            open_close=True,
            change=lsp.TextDocumentSyncKind.Incremental,
            save=lsp.SaveOptions(include_text=False),
        ),
        code_action_provider=lsp.CodeActionOptions(
            code_action_kinds=[
                lsp.CodeActionKind.QuickFix,
            ],
            resolve_provider=False,
        ),
        hover_provider=lsp.HoverOptions(),
        code_lens_provider=lsp.CodeLensOptions(resolve_provider=False),
        execute_command_provider=lsp.ExecuteCommandOptions(
            commands=[
                "devlens.analyzeFile",
                "devlens.showDashboard",
                "devlens.showExplanation",
                "devlens.configureAI",
                "devlens.clearCache",
                "devlens.analyzeWorkspace",
            ]
        ),
    )


# ---------------------------------------------------------------------------
# Server start functions
# ---------------------------------------------------------------------------

def create_server() -> DevLensLanguageServer:
    """Create a DevLens Language Server instance."""
    server = DevLensLanguageServer(
        name="devlens",
        version="0.8.0",
    )
    return server


def start_server(
    mode: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 2087,
    log_level: str = "info",
) -> None:
    """Start the DevLens Language Server.

    Args:
        mode: Transport mode — 'stdio' or 'tcp'.
        host: TCP host (only used in tcp mode).
        port: TCP port (only used in tcp mode).
        log_level: Logging level (debug, info, warning, error).
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    server = create_server()

    logger.info("Starting DevLens LSP server (mode=%s)", mode)

    if mode == "tcp":
        logger.info("Listening on %s:%d", host, port)
        server.start_tcp(host, port)
    else:
        logger.info("Using STDIO transport")
        server.start_io()


# ---------------------------------------------------------------------------
# Entry point: python -m devlens.language_server
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="DevLens Language Server Protocol (LSP) server",
    )
    parser.add_argument(
        "--mode",
        choices=["stdio", "tcp"],
        default="stdio",
        help="Transport mode (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="TCP host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=2087,
        help="TCP port (default: 2087)",
    )
    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="Log level (default: info)",
    )

    args = parser.parse_args()
    start_server(
        mode=args.mode,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
