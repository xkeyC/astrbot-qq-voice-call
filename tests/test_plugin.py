import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from maibot_sdk import PluginContext

PLUGIN_PATH = Path(__file__).parents[1] / "plugin.py"
PLUGIN_SPEC = importlib.util.spec_from_file_location("qq_voice_call_plugin", PLUGIN_PATH)
assert PLUGIN_SPEC is not None and PLUGIN_SPEC.loader is not None
PLUGIN_MODULE = importlib.util.module_from_spec(PLUGIN_SPEC)
PLUGIN_SPEC.loader.exec_module(PLUGIN_MODULE)
GATEWAY_NAME = PLUGIN_MODULE.GATEWAY_NAME
create_plugin = PLUGIN_MODULE.create_plugin


def test_entrypoint_loads_with_maibot_isolated_import_semantics() -> None:
    script = f"""
import importlib.util
import sys

plugin_path = {str(PLUGIN_PATH)!r}
plugin_root = plugin_path.rsplit('plugin.py', 1)[0].rstrip('\\\\/')
assert all(
    plugin_root != item.rstrip('\\\\/')
    for item in sys.path
)
spec = importlib.util.spec_from_file_location('isolated_qq_voice_call_plugin', plugin_path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert callable(module.create_plugin)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_release_versions_stay_in_sync() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "_manifest.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    bridge_package = json.loads(
        (root / "bridge" / "napcat-plugin" / "package.json").read_text(encoding="utf-8")
    )
    package_version = (root / "maibot_qq_voice_call" / "__init__.py").read_text(
        encoding="utf-8"
    )
    version = manifest["version"]
    assert pyproject["project"]["version"] == version
    assert bridge_package["version"] == version
    assert f'__version__ = "{version}"' in package_version


@pytest.mark.asyncio
async def test_disabled_plugin_completes_lifecycle_without_starting_runtime() -> None:
    calls = []

    async def rpc_call(method, plugin_id, payload, **kwargs):
        calls.append((method, plugin_id, payload))
        return {"success": True}

    plugin = create_plugin()
    plugin._set_context(
        PluginContext("github.claudiagardner.maibot-qq-voice-call", rpc_call=rpc_call)
    )
    plugin.set_plugin_config(plugin.get_default_config())
    await plugin.on_load()
    assert plugin._runtime is None
    await plugin.on_unload()
    assert [call[0] for call in calls] == [
        "host.update_message_gateway_state",
        "host.update_message_gateway_state",
    ]
    assert all(call[2]["gateway_name"] == GATEWAY_NAME for call in calls)
    assert all(call[2]["ready"] is False for call in calls)


def test_manifest_declares_every_host_capability_used_by_runtime() -> None:
    manifest = json.loads(
        (Path(__file__).parents[1] / "_manifest.json").read_text(encoding="utf-8")
    )
    assert set(manifest["capabilities"]) == {
        "llm.generate",
        "llm.transcribe_audio",
        "config.get",
        "chat.open_session",
        "maisaka.context.append",
        "message.get_recent",
        "message.build_readable",
        "person.get_id",
        "person.get_value",
    }


@pytest.mark.asyncio
async def test_archive_command_silently_intercepts_synthetic_message() -> None:
    plugin = create_plugin()
    handled, response, intercept_level = await plugin.handle_call_archive(
        processed_plain_text="[QQ语音通话记录]\n通话摘要：测试"
    )
    assert handled is True
    assert response is None
    assert intercept_level == 1


def test_plugin_registers_archive_command() -> None:
    plugin = create_plugin()
    components = plugin.get_components()
    assert any(
        component["type"] == "COMMAND"
        and component["name"] == "qq_voice_call_archive"
        for component in components
    )
