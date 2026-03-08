"""AI-Powered Code Review — LLM-based intelligent analysis for DevLens.

Integrates with OpenAI and Anthropic APIs to provide intelligent code
review comments, bug pattern detection, refactoring suggestions,
commit message generation, and code explanation.

Also functions as a DevLens plugin (PluginType.ANALYZER) so it
participates in the standard plugin lifecycle when enabled.

Features
--------
- Multi-provider LLM support (OpenAI GPT-4o, Anthropic Claude)
- Language-aware system prompts
- Async HTTP via httpx with retry & rate limiting
- Token counting and budget tracking
- Response caching to avoid redundant API calls
- Graceful fallback when API is unavailable
- Structured output parsing (JSON mode)

Usage (standalone)::

    from devlens.ai_review import AIReviewer

    reviewer = AIReviewer(config)
    comments = await reviewer.review_file(path, diff=diff_text)
    fixes = await reviewer.suggest_fixes(path, issues)
    msg = await reviewer.generate_commit_message(diff)
    explanation = await reviewer.explain_code(code_snippet)

Usage (as plugin)::

    # Automatically discovered when ai_review is in enabled_plugins
    # Runs during the standard plugin lifecycle
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("devlens.ai_review")

# ─── Lazy imports for optional deps ──────────────────────────────────

_httpx = None


def _get_httpx():
    global _httpx
    if _httpx is None:
        try:
            import httpx
            _httpx = httpx
        except ImportError:
            raise ImportError(
                "httpx is required for AI review. "
                "Install with: pip install devlens[ai]"
            )
    return _httpx


# ─── Plugin integration (lazy to avoid circular imports) ─────────────

def _get_plugin_classes():
    """Lazy import plugin classes to avoid circular dependency."""
    from devlens.plugins import (
        FileResult,
        PluginBase,
        PluginContext,
        PluginMeta,
        PluginType,
        PluginConfigField,
        register_plugin,
    )
    return {
        "FileResult": FileResult,
        "PluginBase": PluginBase,
        "PluginContext": PluginContext,
        "PluginMeta": PluginMeta,
        "PluginType": PluginType,
        "PluginConfigField": PluginConfigField,
        "register_plugin": register_plugin,
    }


# ─── Enums ───────────────────────────────────────────────────────────


class AIProvider(str, Enum):
    """Supported LLM providers."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class ReviewMode(str, Enum):
    """Types of AI review operations."""
    REVIEW = "review"
    SUGGEST_FIXES = "suggest_fixes"
    EXPLAIN = "explain"
    COMMIT_MSG = "commit_msg"
    BUG_DETECT = "bug_detect"
    REFACTOR = "refactor"


# ─── Configuration ───────────────────────────────────────────────────


@dataclass
class AIReviewConfig:
    """Configuration for the AI review system."""

    provider: AIProvider = AIProvider.OPENAI
    model: str = ""  # empty = use provider default
    api_key: str = ""
    api_base_url: str = ""
    max_tokens: int = 4096
    temperature: float = 0.3
    timeout: float = 60.0
    max_retries: int = 3
    rate_limit_rpm: int = 60  # requests per minute
    rate_limit_tpm: int = 100_000  # tokens per minute
    cache_enabled: bool = True
    cache_dir: str = ".devlens-cache/ai"
    cache_ttl: int = 86400  # 24 hours
    max_file_size: int = 50_000  # chars
    context_lines: int = 5
    languages: Tuple[str, ...] = ()  # empty = all
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AIReviewConfig":
        """Create config from a dictionary."""
        provider_str = data.get("provider", "openai")
        try:
            provider = AIProvider(provider_str)
        except ValueError:
            logger.warning("Unknown provider '%s', defaulting to openai", provider_str)
            provider = AIProvider.OPENAI

        return cls(
            provider=provider,
            model=data.get("model", ""),
            api_key=data.get("api_key", "") or os.environ.get(
                "DEVLENS_AI_API_KEY",
                os.environ.get("OPENAI_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
            ),
            api_base_url=data.get("api_base_url", ""),
            max_tokens=data.get("max_tokens", 4096),
            temperature=data.get("temperature", 0.3),
            timeout=data.get("timeout", 60.0),
            max_retries=data.get("max_retries", 3),
            rate_limit_rpm=data.get("rate_limit_rpm", 60),
            rate_limit_tpm=data.get("rate_limit_tpm", 100_000),
            cache_enabled=data.get("cache_enabled", True),
            cache_dir=data.get("cache_dir", ".devlens-cache/ai"),
            cache_ttl=data.get("cache_ttl", 86400),
            max_file_size=data.get("max_file_size", 50_000),
            context_lines=data.get("context_lines", 5),
            languages=tuple(data.get("languages", [])),
            enabled=data.get("enabled", True),
        )

    @property
    def default_model(self) -> str:
        if self.model:
            return self.model
        if self.provider == AIProvider.OPENAI:
            return "gpt-4o"
        elif self.provider == AIProvider.ANTHROPIC:
            return "claude-sonnet-4-20250514"
        return "gpt-4o"


# ─── Token Counter ───────────────────────────────────────────────────


class TokenCounter:
    """Track token usage across requests."""

    def __init__(self) -> None:
        self._prompt_tokens: int = 0
        self._completion_tokens: int = 0
        self._total_requests: int = 0
        self._cached_requests: int = 0

    def record(self, prompt_tokens: int, completion_tokens: int, cached: bool = False) -> None:
        self._prompt_tokens += prompt_tokens
        self._completion_tokens += completion_tokens
        self._total_requests += 1
        if cached:
            self._cached_requests += 1

    @property
    def total_tokens(self) -> int:
        return self._prompt_tokens + self._completion_tokens

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "total_tokens": self.total_tokens,
            "total_requests": self._total_requests,
            "cached_requests": self._cached_requests,
        }

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate (~4 chars per token for English)."""
        return max(1, len(text) // 4)

    def reset(self) -> None:
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_requests = 0
        self._cached_requests = 0


# ─── Rate Limiter ────────────────────────────────────────────────────


class RateLimiter:
    """Simple sliding-window rate limiter."""

    def __init__(self, rpm: int = 60, tpm: int = 100_000) -> None:
        self._rpm = rpm
        self._tpm = tpm
        self._request_times: List[float] = []
        self._token_log: List[Tuple[float, int]] = []

    async def acquire(self, estimated_tokens: int = 0) -> None:
        """Wait until the request can proceed without exceeding limits."""
        now = time.monotonic()

        # Clean old entries (>60s)
        cutoff = now - 60.0
        self._request_times = [t for t in self._request_times if t > cutoff]
        self._token_log = [(t, n) for t, n in self._token_log if t > cutoff]

        # Check RPM
        while len(self._request_times) >= self._rpm:
            wait = self._request_times[0] - cutoff
            if wait > 0:
                logger.debug("Rate limit: waiting %.1fs (RPM)", wait)
                await asyncio.sleep(wait)
            now = time.monotonic()
            cutoff = now - 60.0
            self._request_times = [t for t in self._request_times if t > cutoff]

        # Check TPM
        current_tokens = sum(n for _, n in self._token_log)
        while current_tokens + estimated_tokens > self._tpm:
            if self._token_log:
                wait = self._token_log[0][0] - cutoff
                if wait > 0:
                    logger.debug("Rate limit: waiting %.1fs (TPM)", wait)
                    await asyncio.sleep(wait)
            now = time.monotonic()
            cutoff = now - 60.0
            self._token_log = [(t, n) for t, n in self._token_log if t > cutoff]
            current_tokens = sum(n for _, n in self._token_log)

        self._request_times.append(now)

    def record_tokens(self, count: int) -> None:
        self._token_log.append((time.monotonic(), count))


# ─── Response Cache ──────────────────────────────────────────────────


class ResponseCache:
    """File-based cache for AI responses."""

    def __init__(self, cache_dir: str, ttl: int = 86400) -> None:
        self._cache_dir = Path(cache_dir)
        self._ttl = ttl

    def _key(self, prompt: str, model: str) -> str:
        content = f"{model}:{prompt}"
        return hashlib.sha256(content.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def get(self, prompt: str, model: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached response if valid."""
        key = self._key(prompt, model)
        path = self._path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - data.get("timestamp", 0) > self._ttl:
                path.unlink(missing_ok=True)
                return None
            logger.debug("Cache hit: %s", key[:12])
            return data.get("response")
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, prompt: str, model: str, response: Dict[str, Any]) -> None:
        """Store a response in the cache."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        key = self._key(prompt, model)
        path = self._path(key)
        try:
            data = {"timestamp": time.time(), "model": model, "response": response}
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Cache write failed: %s", exc)

    def clear(self) -> int:
        """Remove all cached entries. Returns count removed."""
        count = 0
        if self._cache_dir.exists():
            for f in self._cache_dir.glob("*.json"):
                f.unlink(missing_ok=True)
                count += 1
        return count

    def prune_expired(self) -> int:
        """Remove expired entries. Returns count removed."""
        count = 0
        if not self._cache_dir.exists():
            return count
        now = time.time()
        for f in self._cache_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if now - data.get("timestamp", 0) > self._ttl:
                    f.unlink(missing_ok=True)
                    count += 1
            except (json.JSONDecodeError, OSError):
                f.unlink(missing_ok=True)
                count += 1
        return count


# ─── Language-Aware System Prompts ───────────────────────────────────

SYSTEM_PROMPTS: Dict[str, str] = {
    "default": (
        "You are an expert code reviewer for DevLens, a comprehensive code analysis tool. "
        "Provide precise, actionable feedback focused on bugs, security issues, performance, "
        "and maintainability. Be concise. Reference specific line numbers when possible. "
        "Format output as JSON when requested."
    ),
    "python": (
        "You are an expert Python code reviewer. Focus on Pythonic idioms, PEP 8 compliance, "
        "type hint usage, proper exception handling, and common Python anti-patterns. "
        "Check for security issues like eval/exec usage, SQL injection, path traversal. "
        "Suggest modern Python 3.10+ features where appropriate."
    ),
    "javascript": (
        "You are an expert JavaScript/TypeScript code reviewer. Focus on modern ES2022+ patterns, "
        "proper async/await usage, XSS prevention, prototype pollution, and common JS pitfalls. "
        "Check for proper error handling, memory leaks, and performance anti-patterns. "
        "Suggest TypeScript improvements where applicable."
    ),
    "typescript": (
        "You are an expert TypeScript code reviewer. Focus on type safety, proper generic usage, "
        "discriminated unions, and strict mode compliance. Check for 'any' type abuse, "
        "proper null handling, and modern TypeScript 5.x features."
    ),
    "java": (
        "You are an expert Java code reviewer. Focus on SOLID principles, proper exception "
        "handling, thread safety, resource management (try-with-resources), and common "
        "Java anti-patterns. Check for null safety and suggest modern Java 17+ features."
    ),
    "go": (
        "You are an expert Go code reviewer. Focus on idiomatic Go patterns, proper error "
        "handling (no ignored errors), goroutine/channel safety, and effective Go guidelines. "
        "Check for race conditions, resource leaks, and suggest standard library usage."
    ),
    "rust": (
        "You are an expert Rust code reviewer. Focus on ownership/borrowing patterns, "
        "proper error handling with Result/Option, unsafe code review, and idiomatic Rust. "
        "Check for potential panics, clippy warnings, and suggest zero-cost abstractions."
    ),
}

REVIEW_PROMPT_TEMPLATE = """Review the following {language} code and provide feedback.

File: {filepath}
{diff_section}
```{language}
{code}
```

Provide your review as a JSON array of objects with these fields:
- "line": line number (int)
- "severity": "error" | "warning" | "info" | "suggestion"
- "category": "bug" | "security" | "performance" | "style" | "maintainability"
- "message": concise description of the issue
- "suggestion": suggested fix or improvement (optional)

Only report genuine issues. Be precise and actionable."""

BUG_DETECT_PROMPT = """Analyze this {language} code for potential bugs and logic errors.

File: {filepath}
```{language}
{code}
```

Focus on:
1. Off-by-one errors
2. Null/undefined reference risks
3. Resource leaks
4. Race conditions
5. Logic errors
6. Edge cases not handled

Return JSON array with: line, severity, description, suggested_fix."""

REFACTOR_PROMPT = """Suggest refactoring improvements for this {language} code.

File: {filepath}
```{language}
{code}
```

Focus on:
1. Code duplication
2. Long methods (>30 lines)
3. Deep nesting (>3 levels)
4. God classes/functions
5. Missing abstractions
6. Naming improvements

Return JSON array with: line_start, line_end, description, refactored_code."""

COMMIT_MSG_PROMPT = """Generate a conventional commit message for this diff.

Format: <type>(<scope>): <description>

Types: feat, fix, refactor, docs, style, test, chore, perf
Keep the first line under 72 characters.
Add a body with bullet points for significant changes.

Diff:
```
{diff}
```

Return JSON: {{"subject": "...", "body": "..."}}"""

EXPLAIN_PROMPT = """Explain this {language} code clearly and concisely.

```{language}
{code}
```

Provide:
1. A one-paragraph summary of what the code does
2. Key components and their roles
3. Any notable patterns or techniques used
4. Potential issues or improvements

Return as JSON: {{"summary": "...", "components": [...], "patterns": [...], "notes": [...]}}"""


# ─── AI Reviewer Core ────────────────────────────────────────────────


class AIReviewer:
    """Core AI review engine with multi-provider LLM support.

    Parameters
    ----------
    config : dict or AIReviewConfig
        AI review configuration.
    """

    LANGUAGE_MAP = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".jsx": "javascript", ".tsx": "typescript", ".java": "java",
        ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
        ".cs": "csharp", ".cpp": "cpp", ".c": "c", ".swift": "swift",
        ".kt": "kotlin", ".scala": "scala",
    }

    def __init__(self, config: Union[Dict[str, Any], AIReviewConfig]) -> None:
        if isinstance(config, dict):
            self._config = AIReviewConfig.from_dict(config)
        else:
            self._config = config

        self._token_counter = TokenCounter()
        self._rate_limiter = RateLimiter(
            rpm=self._config.rate_limit_rpm,
            tpm=self._config.rate_limit_tpm,
        )
        self._cache = ResponseCache(
            cache_dir=self._config.cache_dir,
            ttl=self._config.cache_ttl,
        ) if self._config.cache_enabled else None

        self._client: Optional[Any] = None  # httpx.AsyncClient, lazy init

    # ── properties ───────────────────────────────────────────────

    @property
    def token_stats(self) -> Dict[str, int]:
        return self._token_counter.stats

    @property
    def config(self) -> AIReviewConfig:
        return self._config

    @property
    def available(self) -> bool:
        """Check if AI review is available (API key configured)."""
        return bool(self._config.api_key and self._config.enabled)

    # ── client management ────────────────────────────────────────

    async def _get_client(self):
        """Lazy-init httpx async client."""
        if self._client is None:
            httpx = _get_httpx()
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._config.timeout),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── language detection ───────────────────────────────────────

    def detect_language(self, path: Path) -> str:
        return self.LANGUAGE_MAP.get(path.suffix.lower(), "unknown")

    def _get_system_prompt(self, language: str) -> str:
        return SYSTEM_PROMPTS.get(language, SYSTEM_PROMPTS["default"])

    # ── LLM API calls ────────────────────────────────────────────

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = True,
    ) -> Dict[str, Any]:
        """Send a request to the configured LLM provider.

        Returns parsed JSON response or {"text": raw_text}.
        """
        if not self.available:
            return {"error": "AI review not available — no API key configured"}

        model = self._config.default_model

        # Check cache
        cache_key_prompt = f"{system_prompt}\n---\n{user_prompt}"
        if self._cache:
            cached = self._cache.get(cache_key_prompt, model)
            if cached is not None:
                self._token_counter.record(0, 0, cached=True)
                return cached

        # Rate limit
        estimated = self._token_counter.estimate_tokens(
            system_prompt + user_prompt
        )
        await self._rate_limiter.acquire(estimated)

        # Dispatch to provider
        try:
            if self._config.provider == AIProvider.OPENAI:
                result = await self._call_openai(system_prompt, user_prompt, model, json_mode)
            elif self._config.provider == AIProvider.ANTHROPIC:
                result = await self._call_anthropic(system_prompt, user_prompt, model, json_mode)
            else:
                return {"error": f"Unsupported provider: {self._config.provider}"}

            # Cache successful response
            if self._cache and "error" not in result:
                self._cache.put(cache_key_prompt, model, result)

            return result

        except Exception as exc:
            logger.error("LLM call failed: %s", exc)
            return {"error": str(exc)}

    async def _call_openai(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        json_mode: bool,
    ) -> Dict[str, Any]:
        """Call OpenAI-compatible API."""
        httpx = _get_httpx()
        client = await self._get_client()

        base_url = self._config.api_base_url or "https://api.openai.com/v1"
        url = f"{base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": self._config.max_tokens,
            "temperature": self._config.temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_exc = None
        for attempt in range(self._config.max_retries):
            try:
                resp = await client.post(url, json=payload, headers=headers)

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("retry-after", 2 ** attempt))
                    logger.warning("Rate limited, retrying in %.1fs", retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                resp.raise_for_status()
                data = resp.json()

                # Extract usage
                usage = data.get("usage", {})
                self._token_counter.record(
                    usage.get("prompt_tokens", estimated := self._token_counter.estimate_tokens(system_prompt + user_prompt)),
                    usage.get("completion_tokens", 0),
                )
                self._rate_limiter.record_tokens(
                    usage.get("total_tokens", estimated)
                )

                # Extract content
                content = data["choices"][0]["message"]["content"]
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    return {"text": content}

            except Exception as exc:
                last_exc = exc
                if attempt < self._config.max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning("OpenAI call failed (attempt %d): %s", attempt + 1, exc)
                    await asyncio.sleep(wait)

        return {"error": f"OpenAI API failed after {self._config.max_retries} retries: {last_exc}"}

    async def _call_anthropic(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        json_mode: bool,
    ) -> Dict[str, Any]:
        """Call Anthropic Messages API."""
        httpx = _get_httpx()
        client = await self._get_client()

        base_url = self._config.api_base_url or "https://api.anthropic.com/v1"
        url = f"{base_url}/messages"

        headers = {
            "x-api-key": self._config.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": self._config.max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if self._config.temperature is not None:
            payload["temperature"] = self._config.temperature

        last_exc = None
        for attempt in range(self._config.max_retries):
            try:
                resp = await client.post(url, json=payload, headers=headers)

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("retry-after", 2 ** attempt))
                    logger.warning("Anthropic rate limited, retrying in %.1fs", retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                resp.raise_for_status()
                data = resp.json()

                # Extract usage
                usage = data.get("usage", {})
                self._token_counter.record(
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                )
                self._rate_limiter.record_tokens(
                    usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                )

                # Extract content
                content_blocks = data.get("content", [])
                text = "".join(
                    block.get("text", "") for block in content_blocks
                    if block.get("type") == "text"
                )
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text}

            except Exception as exc:
                last_exc = exc
                if attempt < self._config.max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning("Anthropic call failed (attempt %d): %s", attempt + 1, exc)
                    await asyncio.sleep(wait)

        return {"error": f"Anthropic API failed after {self._config.max_retries} retries: {last_exc}"}

    # ── high-level review methods ────────────────────────────────

    async def review_file(
        self,
        path: Path,
        diff: str = "",
        code: str = "",
    ) -> List[Dict[str, Any]]:
        """Review a file and return a list of issues.

        Parameters
        ----------
        path : Path
            File path for language detection and reporting.
        diff : str
            Optional diff text for context-aware review.
        code : str
            Source code to review. If empty, reads from *path*.
        """
        if not code:
            try:
                code = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.error("Cannot read %s: %s", path, exc)
                return []

        if len(code) > self._config.max_file_size:
            logger.warning("File %s exceeds max size, truncating", path)
            code = code[: self._config.max_file_size]

        language = self.detect_language(path)
        system_prompt = self._get_system_prompt(language)

        diff_section = f"\nDiff context:\n```\n{diff}\n```\n" if diff else ""
        user_prompt = REVIEW_PROMPT_TEMPLATE.format(
            language=language,
            filepath=str(path),
            diff_section=diff_section,
            code=code,
        )

        result = await self._call_llm(system_prompt, user_prompt)

        if "error" in result:
            logger.warning("Review failed for %s: %s", path, result["error"])
            return []

        # Parse response — could be a list directly or wrapped
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("issues", "review", "comments", "findings"):
                if key in result and isinstance(result[key], list):
                    return result[key]
        return []

    async def suggest_fixes(
        self,
        path: Path,
        issues: List[Dict[str, Any]],
        code: str = "",
    ) -> List[Dict[str, Any]]:
        """Suggest fixes for identified issues."""
        if not code:
            try:
                code = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []

        language = self.detect_language(path)
        system_prompt = self._get_system_prompt(language)

        issues_text = json.dumps(issues, indent=2)
        user_prompt = (
            f"Given these issues found in {path}:\n"
            f"```json\n{issues_text}\n```\n\n"
            f"And the source code:\n```{language}\n{code}\n```\n\n"
            f"Suggest concrete fixes for each issue. Return JSON array with:\n"
            f"- \"issue_index\": index of the original issue\n"
            f"- \"fix_type\": \"replace\" | \"insert\" | \"delete\"\n"
            f"- \"line_start\": starting line number\n"
            f"- \"line_end\": ending line number\n"
            f"- \"original\": original code snippet\n"
            f"- \"replacement\": fixed code\n"
            f"- \"explanation\": brief explanation"
        )

        result = await self._call_llm(system_prompt, user_prompt)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("fixes", "suggestions", "patches"):
                if key in result and isinstance(result[key], list):
                    return result[key]
        return []

    async def detect_bugs(self, path: Path, code: str = "") -> List[Dict[str, Any]]:
        """Specialized bug detection analysis."""
        if not code:
            try:
                code = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []

        language = self.detect_language(path)
        system_prompt = self._get_system_prompt(language)
        user_prompt = BUG_DETECT_PROMPT.format(
            language=language, filepath=str(path), code=code
        )

        result = await self._call_llm(system_prompt, user_prompt)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("bugs", "issues", "findings"):
                if key in result and isinstance(result[key], list):
                    return result[key]
        return []

    async def suggest_refactoring(self, path: Path, code: str = "") -> List[Dict[str, Any]]:
        """Suggest refactoring improvements."""
        if not code:
            try:
                code = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []

        language = self.detect_language(path)
        system_prompt = self._get_system_prompt(language)
        user_prompt = REFACTOR_PROMPT.format(
            language=language, filepath=str(path), code=code
        )

        result = await self._call_llm(system_prompt, user_prompt)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("refactorings", "suggestions", "improvements"):
                if key in result and isinstance(result[key], list):
                    return result[key]
        return []

    async def generate_commit_message(self, diff: str) -> Dict[str, str]:
        """Generate a conventional commit message from a diff."""
        system_prompt = SYSTEM_PROMPTS["default"]
        user_prompt = COMMIT_MSG_PROMPT.format(diff=diff[:10_000])

        result = await self._call_llm(system_prompt, user_prompt)
        if isinstance(result, dict) and "subject" in result:
            return result
        return {"subject": "chore: update code", "body": ""}

    async def explain_code(self, code: str, language: str = "unknown") -> Dict[str, Any]:
        """Generate a human-readable explanation of code."""
        system_prompt = self._get_system_prompt(language)
        user_prompt = EXPLAIN_PROMPT.format(language=language, code=code[:10_000])

        result = await self._call_llm(system_prompt, user_prompt)
        if isinstance(result, dict) and "summary" in result:
            return result
        return {"summary": "Unable to explain code", "components": [], "patterns": [], "notes": []}

    # ── batch operations ─────────────────────────────────────────

    async def review_files(
        self,
        paths: List[Path],
        concurrency: int = 3,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Review multiple files with controlled concurrency."""
        semaphore = asyncio.Semaphore(concurrency)
        results: Dict[str, List[Dict[str, Any]]] = {}

        async def _review_one(p: Path):
            async with semaphore:
                issues = await self.review_file(p)
                results[str(p)] = issues

        tasks = [_review_one(p) for p in paths]
        await asyncio.gather(*tasks, return_exceptions=True)
        return results


# ─── Plugin Wrapper ──────────────────────────────────────────────────


def create_ai_review_plugin():
    """Factory that creates the AI Review plugin class.

    Uses a factory to handle the lazy import of plugin base classes,
    avoiding circular imports.
    """
    pc = _get_plugin_classes()
    PluginBase = pc["PluginBase"]
    PluginMeta = pc["PluginMeta"]
    PluginType = pc["PluginType"]
    PluginContext = pc["PluginContext"]
    FileResult = pc["FileResult"]
    PluginConfigField = pc["PluginConfigField"]
    register_plugin = pc["register_plugin"]

    @register_plugin
    class AIReviewPlugin(PluginBase):
        """AI-powered code review as a DevLens plugin."""

        meta = PluginMeta(
            name="ai-review",
            version="0.7.0",
            description="LLM-powered intelligent code review with multi-provider support",
            author="DevLens",
            plugin_type=PluginType.ANALYZER,
            priority=200,  # runs after basic checkers
            languages=(),  # all languages
            tags=("ai", "review", "llm"),
            min_devlens_version="0.7.0",
        )

        def __init__(self) -> None:
            super().__init__()
            self._reviewer: Optional[AIReviewer] = None
            self._all_issues: Dict[str, List[Dict[str, Any]]] = {}

        def define_config(self) -> list:
            return [
                PluginConfigField(
                    name="provider", field_type=str, default="openai",
                    choices=["openai", "anthropic"],
                    description="LLM provider",
                ),
                PluginConfigField(
                    name="model", field_type=str, default="",
                    description="Model override (empty = provider default)",
                ),
                PluginConfigField(
                    name="api_key", field_type=str, default="",
                    description="API key (or set DEVLENS_AI_API_KEY env var)",
                ),
                PluginConfigField(
                    name="temperature", field_type=float, default=0.3,
                    description="Response temperature (0-1)",
                ),
                PluginConfigField(
                    name="max_tokens", field_type=int, default=4096,
                    description="Max response tokens",
                ),
            ]

        def on_start(self, ctx: PluginContext) -> None:
            ai_config = ctx.config.get("ai_review", {})
            # Merge plugin-specific config
            merged = {**ai_config, **self.config.as_dict()}
            self._reviewer = AIReviewer(merged)

            if not self._reviewer.available:
                logger.warning(
                    "AI Review plugin: no API key configured. "
                    "Set DEVLENS_AI_API_KEY or configure in .devlens.yml"
                )
                self.enabled = False

        def on_file(self, ctx: PluginContext, path: Path) -> Optional[FileResult]:
            if self._reviewer is None or not self._reviewer.available:
                return None

            # Run async review in sync context
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        issues = pool.submit(
                            asyncio.run, self._reviewer.review_file(path)
                        ).result()
                else:
                    issues = loop.run_until_complete(self._reviewer.review_file(path))
            except Exception:
                try:
                    issues = asyncio.run(self._reviewer.review_file(path))
                except Exception as exc:
                    logger.error("AI review failed for %s: %s", path, exc)
                    return None

            if not issues:
                return None

            result = FileResult(path=path)
            for issue in issues:
                result.add_issue(
                    message=issue.get("message", "AI review finding"),
                    line=issue.get("line", 0),
                    severity=issue.get("severity", "info"),
                    rule=f"ai-{issue.get('category', 'review')}",
                    fix=issue.get("suggestion"),
                )

            self._all_issues[str(path)] = issues
            return result

        def on_complete(self, ctx: PluginContext) -> Optional[Dict[str, Any]]:
            stats = self._reviewer.token_stats if self._reviewer else {}
            total_issues = sum(len(v) for v in self._all_issues.values())
            return {
                "total_files_reviewed": len(self._all_issues),
                "total_ai_issues": total_issues,
                "token_usage": stats,
                "provider": self._reviewer.config.provider.value if self._reviewer else "none",
                "model": self._reviewer.config.default_model if self._reviewer else "none",
            }

    return AIReviewPlugin


# ─── Convenience: auto-register plugin on import ─────────────────────

def register_ai_plugin() -> None:
    """Register the AI review plugin. Call during plugin discovery."""
    try:
        create_ai_review_plugin()
    except Exception as exc:
        logger.debug("AI review plugin registration deferred: %s", exc)


# ─── CLI Helpers ─────────────────────────────────────────────────────


def run_ai_review_sync(
    paths: List[Path],
    config: Dict[str, Any],
    mode: ReviewMode = ReviewMode.REVIEW,
) -> Dict[str, Any]:
    """Synchronous wrapper for CLI usage."""
    reviewer = AIReviewer(config.get("ai_review", {}))

    if not reviewer.available:
        return {"error": "AI review not available. Configure API key first."}

    async def _run():
        try:
            if mode == ReviewMode.REVIEW:
                results = await reviewer.review_files(paths)
                return {"mode": "review", "results": results, "stats": reviewer.token_stats}
            elif mode == ReviewMode.COMMIT_MSG:
                # Read all files as diff
                diff_parts = []
                for p in paths:
                    try:
                        diff_parts.append(p.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        pass
                msg = await reviewer.generate_commit_message("\n".join(diff_parts))
                return {"mode": "commit_msg", "result": msg, "stats": reviewer.token_stats}
            elif mode == ReviewMode.EXPLAIN:
                results = {}
                for p in paths:
                    try:
                        code = p.read_text(encoding="utf-8", errors="replace")
                        lang = reviewer.detect_language(p)
                        results[str(p)] = await reviewer.explain_code(code, lang)
                    except OSError:
                        pass
                return {"mode": "explain", "results": results, "stats": reviewer.token_stats}
            elif mode == ReviewMode.BUG_DETECT:
                results = {}
                for p in paths:
                    results[str(p)] = await reviewer.detect_bugs(p)
                return {"mode": "bug_detect", "results": results, "stats": reviewer.token_stats}
            elif mode == ReviewMode.REFACTOR:
                results = {}
                for p in paths:
                    results[str(p)] = await reviewer.suggest_refactoring(p)
                return {"mode": "refactor", "results": results, "stats": reviewer.token_stats}
            elif mode == ReviewMode.SUGGEST_FIXES:
                results = {}
                for p in paths:
                    issues = await reviewer.review_file(p)
                    if issues:
                        results[str(p)] = await reviewer.suggest_fixes(p, issues)
                return {"mode": "suggest_fixes", "results": results, "stats": reviewer.token_stats}
        finally:
            await reviewer.close()

    return asyncio.run(_run())


def configure_api_key(provider: str, api_key: str, config_path: Path) -> bool:
    """Save API key to the DevLens config file."""
    try:
        if config_path.exists():
            import yaml
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        else:
            data = {}

        if "ai_review" not in data:
            data["ai_review"] = {}
        data["ai_review"]["provider"] = provider
        data["ai_review"]["api_key"] = api_key

        import yaml
        config_path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        return True
    except Exception as exc:
        logger.error("Failed to save API key: %s", exc)
        return False
