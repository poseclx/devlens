"""Tests for devlens.plugins module."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from abc import ABC

from devlens.plugins import (
    PluginType,
    PluginMeta,
    PluginBase,
    PluginRegistry,
    PluginError,
    PluginLoadError,
    PluginConfigError,
    check_compatibility,
    create_plugin_template,
    register_plugin,
)


# ---------------------------------------------------------------------------
# PluginType enum tests
# ---------------------------------------------------------------------------

class TestPluginType:
    def test_values(self):
        assert PluginType.CHECKER == "checker"
        assert PluginType.FIXER == "fixer"
        assert PluginType.REPORTER == "reporter"
        assert PluginType.FORMATTER == "formatter"
        assert PluginType.ANALYZER == "analyzer"
        assert PluginType.CUSTOM == "custom"

    def test_all_types_count(self):
        assert len(PluginType) == 6


# ---------------------------------------------------------------------------
# PluginMeta tests
# ---------------------------------------------------------------------------

class TestPluginMeta:
    def test_frozen(self):
        meta = PluginMeta(
            name="test-plugin",
            version="1.0.0",
            plugin_type=PluginType.CHECKER,
            description="A test plugin",
            author="tester",
        )
        with pytest.raises(AttributeError):
            meta.name = "changed"

    def test_fields(self):
        meta = PluginMeta(
            name="my-plugin",
            version="2.1.0",
            plugin_type=PluginType.FIXER,
            description="Fixes things",
            author="dev",
        )
        assert meta.name == "my-plugin"
        assert meta.version == "2.1.0"
        assert meta.plugin_type == PluginType.FIXER


# ---------------------------------------------------------------------------
# PluginBase abstract class tests
# ---------------------------------------------------------------------------

class TestPluginBase:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            PluginBase()

    def test_subclass_must_implement(self):
        class IncompletePlugin(PluginBase):
            pass
        with pytest.raises(TypeError):
            IncompletePlugin()


# ---------------------------------------------------------------------------
# PluginRegistry tests
# ---------------------------------------------------------------------------

class TestPluginRegistry:
    def test_empty_registry(self):
        registry = PluginRegistry()
        assert registry.list_plugins() == []

    def test_register_and_get(self):
        registry = PluginRegistry()
        meta = PluginMeta(
            name="test-checker",
            version="1.0.0",
            plugin_type=PluginType.CHECKER,
            description="Test",
            author="dev",
        )

        class TestChecker(PluginBase):
            plugin_meta = meta
            def initialize(self, context):
                pass
            def execute(self, context):
                return []
            def cleanup(self):
                pass

        registry.register(TestChecker)
        result = registry.get_plugin("test-checker")
        assert result is not None

    def test_list_plugins(self):
        registry = PluginRegistry()
        meta = PluginMeta(
            name="listed-plugin",
            version="1.0.0",
            plugin_type=PluginType.REPORTER,
            description="Listed",
            author="dev",
        )

        class ListedPlugin(PluginBase):
            plugin_meta = meta
            def initialize(self, context):
                pass
            def execute(self, context):
                return []
            def cleanup(self):
                pass

        registry.register(ListedPlugin)
        plugins = registry.list_plugins()
        assert len(plugins) >= 1
        names = [p["name"] if isinstance(p, dict) else p.name for p in plugins]
        assert "listed-plugin" in names

    def test_get_nonexistent(self):
        registry = PluginRegistry()
        result = registry.get_plugin("does-not-exist")
        assert result is None


# ---------------------------------------------------------------------------
# check_compatibility tests
# ---------------------------------------------------------------------------

class TestCheckCompatibility:
    def test_compatible(self):
        meta = PluginMeta(
            name="compat",
            version="1.0.0",
            plugin_type=PluginType.CHECKER,
            description="Test",
            author="dev",
            min_devlens_version="0.5.0",
        )
        assert check_compatibility(meta, "0.8.0") is True

    def test_incompatible(self):
        meta = PluginMeta(
            name="incompat",
            version="1.0.0",
            plugin_type=PluginType.CHECKER,
            description="Test",
            author="dev",
            min_devlens_version="99.0.0",
        )
        assert check_compatibility(meta, "0.8.0") is False


# ---------------------------------------------------------------------------
# create_plugin_template tests
# ---------------------------------------------------------------------------

class TestCreatePluginTemplate:
    def test_creates_file(self, tmp_path):
        result = create_plugin_template(tmp_path, "my-checker", PluginType.CHECKER)
        assert result.exists()

    def test_template_content(self, tmp_path):
        result = create_plugin_template(tmp_path, "my-fixer", PluginType.FIXER)
        content = result.read_text()
        assert "my-fixer" in content or "my_fixer" in content

    def test_different_types(self, tmp_path):
        for ptype in [PluginType.CHECKER, PluginType.FIXER, PluginType.REPORTER]:
            sub = tmp_path / ptype.value
            sub.mkdir()
            result = create_plugin_template(sub, f"test-{ptype.value}", ptype)
            assert result.exists()


# ---------------------------------------------------------------------------
# Exception hierarchy tests
# ---------------------------------------------------------------------------

class TestExceptions:
    def test_plugin_error_is_exception(self):
        assert issubclass(PluginError, Exception)

    def test_load_error_is_plugin_error(self):
        assert issubclass(PluginLoadError, PluginError)

    def test_config_error_is_plugin_error(self):
        assert issubclass(PluginConfigError, PluginError)

    def test_can_raise_and_catch(self):
        with pytest.raises(PluginError):
            raise PluginLoadError("Failed to load")
