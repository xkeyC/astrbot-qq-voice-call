import importlib.util
import json
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
