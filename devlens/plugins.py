"""Plugin System — extensible architecture for DevLens.

Provides a robust plugin framework that allows users to extend DevLens
with custom checkers, fixers, reporters, and formatters.  Plugins can
be discovered via Python entry-points (``devlens.plugins`` group),
local directories, or explicit registration.

Architecture
------------
- **PluginBase**: Abstract base class every plugin must inherit.
- **PluginType**: Enum of recognised plugin categories.
- **PluginMeta**: Frozen dataclass carrying plugin metadata.
- **PluginManager**: Central orchestrator — discovers, validates, loads,
  orders (by priority + dependency), and invokes plugins through their
  lifecycle hooks.
- **PluginRegistry**: Singleton that maps plugin names → classes.
- **PluginConfig**: Schema-validated configuration for each plugin.
- **Lifecycle hooks**: ``on_start``, ``on_file``, ``on_complete``.

Usage (programmatic)::

    from devlens.plugins import PluginManager

    pm = PluginManager(config)
    pm.discover()          # entry-points + local dirs
    pm.load_all()          # instantiate & validate
    pm.run_lifecycle(files) # on_start → on_file × N → on_complete
    results = pm.collect_results()
"""

from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import logging
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    Type,
    Union,
)

logger = logging.getLogger("devlens.plugins")

# ─── Plugin Type Enum ────────────────────────────────────────────────


class PluginType(str, Enum):
    """Recognised plugin categories."""

    CHECKER = "checker"
    FIXER = "fixer"
    REPORTER = "reporter"
    FORMATTER = "formatter"
    ANALYZER = "analyzer"
    CUSTOM = "custom"


# ─── Plugin Metadata ─────────────────────────────────────────────────


@dataclass(frozen=True)
class PluginMeta:
    """Immutable metadata attached to every plugin."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    plugin_type: PluginType = PluginType.CUSTOM
    priority: int = 100  # lower = runs first
    languages: Tuple[str, ...] = ()  # empty = all languages
    dependencies: Tuple[str, ...] = ()  # names of required plugins
    tags: Tuple[str, ...] = ()
    min_devlens_version: str = "0.1.0"
    homepage: str = ""
    license: str = ""


# ─── Plugin Configuration Schema ────────────────────────────────────


@dataclass
class PluginConfigField:
    """Single configuration field definition."""

    name: str
    field_type: type = str
    default: Any = None
    required: bool = False
    description: str = ""
    choices: Optional[Sequence[Any]] = None

    def validate(self, value: Any) -> Any:
        """Validate and coerce *value* to the declared type."""
        if value is None:
            if self.required:
                raise PluginConfigError(
                    f"Required config field '{self.name}' is missing"
                )
            return self.default
        if self.choices and value not in self.choices:
            raise PluginConfigError(
                f"Field '{self.name}' must be one of {self.choices}, got {value!r}"
            )
        try:
            return self.field_type(value)
        except (TypeError, ValueError) as exc:
            raise PluginConfigError(
                f"Field '{self.name}' expected {self.field_type.__name__}, "
                f"got {type(value).__name__}: {exc}"
            ) from exc


@dataclass
class PluginConfig:
    """Validated configuration container for a plugin instance."""

    plugin_name: str
    _fields: Dict[str, PluginConfigField] = field(default_factory=dict)
    _values: Dict[str, Any] = field(default_factory=dict)

    def define(self, cfg_field: PluginConfigField) -> None:
        """Register a configuration field."""
        self._fields[cfg_field.name] = cfg_field

    def load(self, raw: Dict[str, Any]) -> None:
        """Load and validate raw config values."""
        for fname, fdef in self._fields.items():
            self._values[fname] = fdef.validate(raw.get(fname))
        unknown = set(raw) - set(self._fields)
        if unknown:
            logger.warning(
                "Plugin '%s': unknown config keys ignored: %s",
                self.plugin_name,
                ", ".join(sorted(unknown)),
            )

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a validated config value."""
        return self._values.get(key, default)

    def as_dict(self) -> Dict[str, Any]:
        """Return all validated values."""
        return dict(self._values)


# ─── Exceptions ──────────────────────────────────────────────────────


class PluginError(Exception):
    """Base exception for the plugin system."""


class PluginLoadError(PluginError):
    """Raised when a plugin cannot be loaded."""


class PluginConfigError(PluginError):
    """Raised for configuration validation failures."""


class PluginDependencyError(PluginError):
    """Raised when a plugin's dependencies are not met."""


class PluginCompatibilityError(PluginError):
    """Raised when a plugin is incompatible with the current DevLens version."""


# ─── Lifecycle Context ───────────────────────────────────────────────


@dataclass
class PluginContext:
    """Mutable context passed through the plugin lifecycle."""

    project_root: Path = field(default_factory=Path.cwd)
    files: List[Path] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    results: Dict[str, Any] = field(default_factory=dict)
    shared: Dict[str, Any] = field(default_factory=dict)  # inter-plugin data
    errors: List[Tuple[str, Exception]] = field(default_factory=list)

    def add_result(self, plugin_name: str, key: str, value: Any) -> None:
        """Store a result from a plugin."""
        self.results.setdefault(plugin_name, {})[key] = value

    def get_shared(self, key: str, default: Any = None) -> Any:
        """Read inter-plugin shared data."""
        return self.shared.get(key, default)

    def set_shared(self, key: str, value: Any) -> None:
        """Write inter-plugin shared data."""
        self.shared[key] = value


# ─── File Analysis Result ────────────────────────────────────────────


@dataclass
class FileResult:
    """Result of analysing a single file."""

    path: Path
    issues: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    suggestions: List[str] = field(default_factory=list)
    score: Optional[float] = None

    def add_issue(
        self,
        message: str,
        line: int = 0,
        severity: str = "warning",
        rule: str = "",
        fix: Optional[str] = None,
    ) -> None:
        """Append an issue found during analysis."""
        self.issues.append(
            {
                "message": message,
                "line": line,
                "severity": severity,
                "rule": rule,
                "fix": fix,
            }
        )


# ─── Plugin Base Class ───────────────────────────────────────────────


class PluginBase(ABC):
    """Abstract base class every DevLens plugin must inherit.

    Subclasses **must** set the ``meta`` class attribute and implement
    at least one of the lifecycle hooks.  The default implementations
    are no-ops so plugins only override what they need.
    """

    meta: PluginMeta  # must be set by subclass

    def __init__(self) -> None:
        self._enabled: bool = True
        self._config = PluginConfig(plugin_name=self.meta.name)
        self._file_results: Dict[str, FileResult] = {}

    # ── configuration ────────────────────────────────────────────

    def define_config(self) -> List[PluginConfigField]:
        """Override to declare configuration fields."""
        return []

    def configure(self, raw_config: Dict[str, Any]) -> None:
        """Load validated configuration."""
        for f in self.define_config():
            self._config.define(f)
        self._config.load(raw_config)

    @property
    def config(self) -> PluginConfig:
        return self._config

    # ── state ────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def name(self) -> str:
        return self.meta.name

    @property
    def plugin_type(self) -> PluginType:
        return self.meta.plugin_type

    # ── lifecycle hooks ──────────────────────────────────────────

    def on_start(self, ctx: PluginContext) -> None:
        """Called once before file processing begins."""

    def on_file(self, ctx: PluginContext, path: Path) -> Optional[FileResult]:
        """Called for every file in scope.  Return a FileResult or None."""
        return None

    def on_complete(self, ctx: PluginContext) -> Optional[Dict[str, Any]]:
        """Called after all files have been processed.  Return summary dict or None."""
        return None

    # ── convenience methods ──────────────────────────────────────

    def supports_language(self, language: str) -> bool:
        """Check if this plugin handles *language* (empty = all)."""
        if not self.meta.languages:
            return True
        return language.lower() in (l.lower() for l in self.meta.languages)

    def get_file_results(self) -> Dict[str, FileResult]:
        """Retrieve accumulated file results."""
        return dict(self._file_results)

    def _store_file_result(self, path: Path, result: FileResult) -> None:
        """Internal: store a file result."""
        self._file_results[str(path)] = result

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"name={self.meta.name!r} "
            f"type={self.meta.plugin_type.value} "
            f"v{self.meta.version}>"
        )


# ─── Plugin Registry (singleton) ─────────────────────────────────────


class PluginRegistry:
    """Global registry mapping plugin names to their classes."""

    _instance: Optional["PluginRegistry"] = None
    _plugins: Dict[str, Type[PluginBase]]

    def __new__(cls) -> "PluginRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._plugins = {}
        return cls._instance

    def register(self, plugin_cls: Type[PluginBase]) -> Type[PluginBase]:
        """Register a plugin class.  Can be used as a decorator::

            @registry.register
            class MyChecker(PluginBase):
                meta = PluginMeta(name="my-checker", ...)
        """
        if not hasattr(plugin_cls, "meta"):
            raise PluginLoadError(
                f"{plugin_cls.__name__} must define a 'meta' class attribute"
            )
        name = plugin_cls.meta.name
        if name in self._plugins:
            logger.warning(
                "Plugin '%s' already registered — overwriting with %s",
                name,
                plugin_cls.__name__,
            )
        self._plugins[name] = plugin_cls
        logger.debug("Registered plugin: %s (%s)", name, plugin_cls.__name__)
        return plugin_cls

    def unregister(self, name: str) -> bool:
        """Remove a plugin from the registry."""
        if name in self._plugins:
            del self._plugins[name]
            logger.debug("Unregistered plugin: %s", name)
            return True
        return False

    def get(self, name: str) -> Optional[Type[PluginBase]]:
        return self._plugins.get(name)

    def all(self) -> Dict[str, Type[PluginBase]]:
        return dict(self._plugins)

    def names(self) -> List[str]:
        return list(self._plugins.keys())

    def by_type(self, ptype: PluginType) -> Dict[str, Type[PluginBase]]:
        return {
            n: c for n, c in self._plugins.items() if c.meta.plugin_type == ptype
        }

    def clear(self) -> None:
        """Remove all registrations (useful for testing)."""
        self._plugins.clear()

    def __len__(self) -> int:
        return len(self._plugins)

    def __contains__(self, name: str) -> bool:
        return name in self._plugins


# module-level convenience
registry = PluginRegistry()


# ─── Decorator shortcut ──────────────────────────────────────────────


def register_plugin(cls: Type[PluginBase]) -> Type[PluginBase]:
    """Class decorator for registering a plugin::

        @register_plugin
        class MyChecker(PluginBase):
            meta = PluginMeta(name="my-checker", plugin_type=PluginType.CHECKER)
    """
    return registry.register(cls)


# ─── Version Compatibility ───────────────────────────────────────────


def _parse_version(v: str) -> Tuple[int, ...]:
    """Parse a semver-ish string into a comparable tuple."""
    parts: List[int] = []
    for segment in v.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            break
    return tuple(parts)


def check_compatibility(plugin_meta: PluginMeta, devlens_version: str) -> bool:
    """Return True if the plugin is compatible with *devlens_version*."""
    required = _parse_version(plugin_meta.min_devlens_version)
    current = _parse_version(devlens_version)
    return current >= required


# ─── Plugin Manager ──────────────────────────────────────────────────


class PluginManager:
    """Central orchestrator for plugin discovery, loading, and execution.

    Parameters
    ----------
    config : dict
        The full DevLens configuration (``load_config()`` output).
    devlens_version : str
        Current DevLens version for compatibility checks.
    """

    ENTRY_POINT_GROUP = "devlens.plugins"

    def __init__(
        self,
        config: Dict[str, Any],
        devlens_version: str = "0.7.0",
    ) -> None:
        self._config = config
        self._devlens_version = devlens_version
        self._plugin_config: Dict[str, Any] = config.get("plugins", {})
        self._instances: Dict[str, PluginBase] = {}
        self._load_order: List[str] = []
        self._discovered: Dict[str, Type[PluginBase]] = {}
        self._errors: List[Tuple[str, Exception]] = []

    # ── properties ───────────────────────────────────────────────

    @property
    def loaded_plugins(self) -> Dict[str, PluginBase]:
        return dict(self._instances)

    @property
    def load_order(self) -> List[str]:
        return list(self._load_order)

    @property
    def errors(self) -> List[Tuple[str, Exception]]:
        return list(self._errors)

    # ── discovery ────────────────────────────────────────────────

    def discover(self) -> int:
        """Discover plugins from entry-points and local directories.

        Returns the total number of discovered plugin classes.
        """
        count = 0
        count += self._discover_entry_points()
        count += self._discover_local_dirs()
        count += self._discover_registry()
        logger.info("Discovered %d plugin(s)", count)
        return count

    def _discover_entry_points(self) -> int:
        """Scan installed packages for devlens.plugins entry-points."""
        count = 0
        try:
            eps = importlib.metadata.entry_points()
            # Python 3.12+ returns a SelectableGroups / dict
            if hasattr(eps, "select"):
                group = eps.select(group=self.ENTRY_POINT_GROUP)
            elif isinstance(eps, dict):
                group = eps.get(self.ENTRY_POINT_GROUP, [])
            else:
                group = [ep for ep in eps if ep.group == self.ENTRY_POINT_GROUP]

            for ep in group:
                try:
                    plugin_cls = ep.load()
                    if self._validate_class(plugin_cls):
                        self._discovered[plugin_cls.meta.name] = plugin_cls
                        count += 1
                        logger.debug(
                            "Discovered entry-point plugin: %s", plugin_cls.meta.name
                        )
                except Exception as exc:
                    self._errors.append((ep.name, exc))
                    logger.warning(
                        "Failed to load entry-point '%s': %s", ep.name, exc
                    )
        except Exception as exc:
            logger.warning("Entry-point discovery failed: %s", exc)
        return count

    def _discover_local_dirs(self) -> int:
        """Scan local plugin directories for Python modules."""
        count = 0
        plugin_dirs: List[str] = []

        # From config
        cfg_dir = self._plugin_config.get("plugin_dir", "")
        if cfg_dir:
            plugin_dirs.append(cfg_dir)

        # Default locations
        for default_dir in [".devlens-plugins", "devlens_plugins"]:
            if os.path.isdir(default_dir) and default_dir not in plugin_dirs:
                plugin_dirs.append(default_dir)

        for pdir in plugin_dirs:
            count += self._scan_directory(Path(pdir))
        return count

    def _scan_directory(self, directory: Path) -> int:
        """Import all .py files from *directory* and collect plugin classes."""
        count = 0
        if not directory.is_dir():
            return count

        # Add directory to sys.path temporarily
        dir_str = str(directory.resolve())
        added_to_path = False
        if dir_str not in sys.path:
            sys.path.insert(0, dir_str)
            added_to_path = True

        try:
            for py_file in sorted(directory.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue
                module_name = py_file.stem
                try:
                    spec = importlib.util.spec_from_file_location(
                        f"devlens_plugin_{module_name}", str(py_file)
                    )
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        # Find PluginBase subclasses in the module
                        for attr_name in dir(module):
                            attr = getattr(module, attr_name)
                            if (
                                inspect.isclass(attr)
                                and issubclass(attr, PluginBase)
                                and attr is not PluginBase
                                and hasattr(attr, "meta")
                            ):
                                self._discovered[attr.meta.name] = attr
                                count += 1
                                logger.debug(
                                    "Discovered local plugin: %s from %s",
                                    attr.meta.name,
                                    py_file,
                                )
                except Exception as exc:
                    self._errors.append((str(py_file), exc))
                    logger.warning(
                        "Failed to load plugin from '%s': %s", py_file, exc
                    )
        finally:
            if added_to_path:
                sys.path.remove(dir_str)
        return count

    def _discover_registry(self) -> int:
        """Collect any plugins already in the global registry."""
        count = 0
        for name, cls in registry.all().items():
            if name not in self._discovered:
                self._discovered[name] = cls
                count += 1
        return count

    # ── validation ───────────────────────────────────────────────

    def _validate_class(self, cls: Any) -> bool:
        """Ensure *cls* is a valid PluginBase subclass."""
        if not inspect.isclass(cls):
            return False
        if not issubclass(cls, PluginBase):
            return False
        if not hasattr(cls, "meta") or not isinstance(cls.meta, PluginMeta):
            return False
        return True

    def _check_dependencies(self, plugin_cls: Type[PluginBase]) -> bool:
        """Verify all declared dependencies are available."""
        missing = []
        for dep in plugin_cls.meta.dependencies:
            if dep not in self._discovered and dep not in self._instances:
                missing.append(dep)
        if missing:
            self._errors.append(
                (
                    plugin_cls.meta.name,
                    PluginDependencyError(
                        f"Missing dependencies: {', '.join(missing)}"
                    ),
                )
            )
            logger.warning(
                "Plugin '%s' skipped — missing deps: %s",
                plugin_cls.meta.name,
                ", ".join(missing),
            )
            return False
        return True

    # ── loading ──────────────────────────────────────────────────

    def load_all(self) -> int:
        """Instantiate and configure all discovered plugins.

        Returns the number of successfully loaded plugins.
        """
        enabled_list = self._plugin_config.get("enabled_plugins", [])
        auto_discover = self._plugin_config.get("auto_discover", True)

        # Determine which plugins to load
        to_load: Dict[str, Type[PluginBase]] = {}
        for name, cls in self._discovered.items():
            if enabled_list and name not in enabled_list:
                logger.debug("Plugin '%s' not in enabled list — skipping", name)
                continue
            if not auto_discover and name not in enabled_list:
                continue
            to_load[name] = cls

        # Topological sort by dependencies + priority
        ordered = self._topological_sort(to_load)

        loaded = 0
        for name in ordered:
            cls = to_load[name]

            # Compatibility check
            if not check_compatibility(cls.meta, self._devlens_version):
                self._errors.append(
                    (
                        name,
                        PluginCompatibilityError(
                            f"Requires DevLens >= {cls.meta.min_devlens_version}, "
                            f"current is {self._devlens_version}"
                        ),
                    )
                )
                logger.warning(
                    "Plugin '%s' incompatible with DevLens %s",
                    name,
                    self._devlens_version,
                )
                continue

            # Dependency check
            if not self._check_dependencies(cls):
                continue

            # Instantiate
            try:
                instance = cls()
                # Load plugin-specific config
                plugin_raw_config = self._plugin_config.get(name, {})
                instance.configure(plugin_raw_config)
                self._instances[name] = instance
                self._load_order.append(name)
                loaded += 1
                logger.info(
                    "Loaded plugin: %s v%s (%s)",
                    name,
                    cls.meta.version,
                    cls.meta.plugin_type.value,
                )
            except Exception as exc:
                self._errors.append((name, exc))
                logger.error("Failed to instantiate plugin '%s': %s", name, exc)

        return loaded

    def load_single(self, name: str) -> bool:
        """Load a single plugin by name."""
        cls = self._discovered.get(name) or registry.get(name)
        if cls is None:
            logger.warning("Plugin '%s' not found", name)
            return False
        try:
            instance = cls()
            plugin_raw_config = self._plugin_config.get(name, {})
            instance.configure(plugin_raw_config)
            self._instances[name] = instance
            self._load_order.append(name)
            return True
        except Exception as exc:
            self._errors.append((name, exc))
            logger.error("Failed to load plugin '%s': %s", name, exc)
            return False

    def unload(self, name: str) -> bool:
        """Unload a plugin by name."""
        if name in self._instances:
            del self._instances[name]
            self._load_order = [n for n in self._load_order if n != name]
            logger.info("Unloaded plugin: %s", name)
            return True
        return False

    # ── topological sort ─────────────────────────────────────────

    def _topological_sort(
        self, plugins: Dict[str, Type[PluginBase]]
    ) -> List[str]:
        """Sort plugins respecting dependencies and priority."""
        # Build adjacency: plugin → set of plugins that must come before it
        in_degree: Dict[str, int] = {n: 0 for n in plugins}
        dependants: Dict[str, List[str]] = {n: [] for n in plugins}

        for name, cls in plugins.items():
            for dep in cls.meta.dependencies:
                if dep in plugins:
                    in_degree[name] += 1
                    dependants[dep].append(name)

        # Kahn's algorithm with priority tie-breaking
        queue: List[Tuple[int, str]] = sorted(
            [(plugins[n].meta.priority, n) for n in plugins if in_degree[n] == 0]
        )
        result: List[str] = []

        while queue:
            queue.sort()  # priority ordering
            _, current = queue.pop(0)
            result.append(current)
            for dependent in dependants[current]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append((plugins[dependent].meta.priority, dependent))

        # Detect cycles
        if len(result) != len(plugins):
            cycle_members = [n for n in plugins if n not in result]
            logger.warning(
                "Dependency cycle detected among: %s", ", ".join(cycle_members)
            )
            # Append remaining in priority order
            remaining = sorted(cycle_members, key=lambda n: plugins[n].meta.priority)
            result.extend(remaining)

        return result

    # ── lifecycle execution ──────────────────────────────────────

    def run_lifecycle(
        self,
        files: List[Path],
        project_root: Optional[Path] = None,
    ) -> PluginContext:
        """Execute the full plugin lifecycle: start → file × N → complete.

        Returns the populated PluginContext.
        """
        ctx = PluginContext(
            project_root=project_root or Path.cwd(),
            files=files,
            config=self._config,
        )

        # on_start
        for name in self._load_order:
            plugin = self._instances[name]
            if not plugin.enabled:
                continue
            try:
                plugin.on_start(ctx)
            except Exception as exc:
                ctx.errors.append((name, exc))
                logger.error("Plugin '%s' on_start failed: %s", name, exc)

        # on_file
        for fpath in files:
            for name in self._load_order:
                plugin = self._instances[name]
                if not plugin.enabled:
                    continue
                # Language filtering
                lang = self._detect_language(fpath)
                if not plugin.supports_language(lang):
                    continue
                try:
                    result = plugin.on_file(ctx, fpath)
                    if result is not None:
                        plugin._store_file_result(fpath, result)
                        ctx.add_result(name, str(fpath), result)
                except Exception as exc:
                    ctx.errors.append((name, exc))
                    logger.error(
                        "Plugin '%s' on_file failed for %s: %s", name, fpath, exc
                    )

        # on_complete
        for name in self._load_order:
            plugin = self._instances[name]
            if not plugin.enabled:
                continue
            try:
                summary = plugin.on_complete(ctx)
                if summary is not None:
                    ctx.add_result(name, "__summary__", summary)
            except Exception as exc:
                ctx.errors.append((name, exc))
                logger.error("Plugin '%s' on_complete failed: %s", name, exc)

        return ctx

    def collect_results(self) -> Dict[str, Dict[str, FileResult]]:
        """Gather file results from all loaded plugins."""
        return {
            name: plugin.get_file_results()
            for name, plugin in self._instances.items()
        }

    # ── helpers ──────────────────────────────────────────────────

    @staticmethod
    def _detect_language(path: Path) -> str:
        """Map file extension to language name."""
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".jsx": "javascript",
            ".tsx": "typescript",
            ".java": "java",
            ".go": "go",
            ".rs": "rust",
            ".rb": "ruby",
            ".php": "php",
            ".cs": "csharp",
            ".cpp": "cpp",
            ".c": "c",
            ".swift": "swift",
            ".kt": "kotlin",
            ".scala": "scala",
            ".sh": "bash",
            ".yml": "yaml",
            ".yaml": "yaml",
            ".json": "json",
            ".toml": "toml",
            ".md": "markdown",
        }
        return ext_map.get(path.suffix.lower(), "unknown")

    def get_plugin(self, name: str) -> Optional[PluginBase]:
        """Retrieve a loaded plugin instance by name."""
        return self._instances.get(name)

    def get_plugins_by_type(self, ptype: PluginType) -> List[PluginBase]:
        """Return loaded plugins of a specific type."""
        return [
            p
            for p in self._instances.values()
            if p.meta.plugin_type == ptype
        ]

    def plugin_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Return human-readable info about a plugin."""
        inst = self._instances.get(name)
        cls = self._discovered.get(name) or registry.get(name)
        meta = inst.meta if inst else (cls.meta if cls else None)
        if meta is None:
            return None
        return {
            "name": meta.name,
            "version": meta.version,
            "type": meta.plugin_type.value,
            "description": meta.description,
            "author": meta.author,
            "priority": meta.priority,
            "languages": list(meta.languages) or ["all"],
            "dependencies": list(meta.dependencies),
            "tags": list(meta.tags),
            "min_devlens_version": meta.min_devlens_version,
            "homepage": meta.homepage,
            "license": meta.license,
            "loaded": name in self._instances,
            "enabled": inst.enabled if inst else None,
        }

    def list_plugins(self) -> List[Dict[str, Any]]:
        """List all discovered plugins with their load status."""
        result = []
        all_names = set(self._discovered.keys()) | set(self._instances.keys())
        for name in sorted(all_names):
            info = self.plugin_info(name)
            if info:
                result.append(info)
        return result

    def enable(self, name: str) -> bool:
        """Enable a loaded plugin."""
        inst = self._instances.get(name)
        if inst:
            inst.enabled = True
            logger.info("Enabled plugin: %s", name)
            return True
        return False

    def disable(self, name: str) -> bool:
        """Disable a loaded plugin (keeps it loaded but skips execution)."""
        inst = self._instances.get(name)
        if inst:
            inst.enabled = False
            logger.info("Disabled plugin: %s", name)
            return True
        return False


# ─── Plugin Installation Helpers ─────────────────────────────────────


def install_plugin_from_pip(package: str) -> bool:
    """Install a plugin package via pip.

    Parameters
    ----------
    package : str
        Package spec (e.g. ``devlens-plugin-mycheck`` or
        ``devlens-plugin-mycheck==1.2.0``).

    Returns True on success.
    """
    import subprocess

    logger.info("Installing plugin package: %s", package)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package],
            capture_output=True,
            text=True,
            check=True,
        )
        logger.info("Installed: %s", package)
        return True
    except subprocess.CalledProcessError as exc:
        logger.error("pip install failed: %s\n%s", exc, exc.stderr)
        return False


def uninstall_plugin_from_pip(package: str) -> bool:
    """Uninstall a plugin package via pip."""
    import subprocess

    logger.info("Uninstalling plugin package: %s", package)
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "-y", package],
            capture_output=True,
            text=True,
            check=True,
        )
        logger.info("Uninstalled: %s", package)
        return True
    except subprocess.CalledProcessError as exc:
        logger.error("pip uninstall failed: %s\n%s", exc, exc.stderr)
        return False


def create_plugin_template(directory: Path, name: str, ptype: PluginType) -> Path:
    """Scaffold a new plugin file from a template."""
    safe_name = name.replace("-", "_")
    class_name = "".join(w.capitalize() for w in name.split("-")) + "Plugin"
    filepath = directory / f"{safe_name}.py"

    template = f'\"\"\"DevLens plugin: {name}.\"\"\"\n\nfrom devlens.plugins import (\n    FileResult,\n    PluginBase,\n    PluginContext,\n    PluginMeta,\n    PluginType,\n    register_plugin,\n)\nfrom pathlib import Path\nfrom typing import Dict, Any, Optional\n\n\n@register_plugin\nclass {class_name}(PluginBase):\n    \"\"\"TODO: Describe what this plugin does.\"\"\"\n\n    meta = PluginMeta(\n        name=\"{name}\",\n        version=\"0.1.0\",\n        description=\"TODO: Add description\",\n        plugin_type=PluginType.{ptype.name},\n        priority=100,\n        languages=(),  # empty = all languages\n    )\n\n    def on_start(self, ctx: PluginContext) -> None:\n        \"\"\"Initialise state before file processing.\"\"\"\n        pass\n\n    def on_file(self, ctx: PluginContext, path: Path) -> Optional[FileResult]:\n        \"\"\"Analyse a single file.\"\"\"\n        result = FileResult(path=path)\n        # TODO: Implement analysis\n        return result\n\n    def on_complete(self, ctx: PluginContext) -> Optional[Dict[str, Any]]:\n        \"\"\"Summarise results after all files processed.\"\"\"\n        return {{}}\n'

    directory.mkdir(parents=True, exist_ok=True)
    filepath.write_text(template, encoding="utf-8")
    logger.info("Created plugin template: %s", filepath)
    return filepath
