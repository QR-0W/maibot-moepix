from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock, patch

import sys


def _load_plugin_module():
    aiohttp = ModuleType("aiohttp")
    aiohttp.ClientError = type("ClientError", (Exception,), {})
    aiohttp.ClientSession = object
    aiohttp.ClientTimeout = Mock

    def decorator(*_args, **_kwargs):
        return lambda function: function

    def field(*, default=None, default_factory=None, **_kwargs):
        return default_factory() if default_factory else default

    maibot_sdk = ModuleType("maibot_sdk")
    maibot_sdk.Command = decorator
    maibot_sdk.Field = field
    maibot_sdk.MaiBotPlugin = object
    maibot_sdk.PluginConfigBase = object
    maibot_sdk.Tool = decorator

    sdk_types = ModuleType("maibot_sdk.types")
    sdk_types.ActivationType = SimpleNamespace(ALWAYS="always")
    sdk_types.ToolParameterInfo = Mock
    sdk_types.ToolParamType = SimpleNamespace(ARRAY="array", BOOLEAN="boolean", INTEGER="integer", STRING="string")

    module_path = Path(__file__).resolve().parents[1] / "plugin.py"
    spec = spec_from_file_location("moepix_plugin_test", module_path)
    module = module_from_spec(spec)
    with patch.dict(sys.modules, {"aiohttp": aiohttp, "maibot_sdk": maibot_sdk, "maibot_sdk.types": sdk_types}):
        spec.loader.exec_module(module)
    return module


PLUGIN = _load_plugin_module()


def _item(pid, page, title):
    return {
        "pid": pid,
        "p": page,
        "title": title,
        "author": "作者",
        "tags": ["标签"],
        "r18": False,
        "aiType": 1,
        "urls": {"regular": "https://example.com/" + title + ".jpg"},
    }


class SamplingTest(TestCase):
    def test_select_random_setu_returns_none_for_empty_candidates(self):
        self.assertIsNone(PLUGIN.SetuPlugin._select_random_setu([]))

    def test_select_random_setu_deduplicates_same_pixiv_page(self):
        duplicate = _item(100, 0, "重复项")
        other = _item(200, 0, "另一项")

        with patch.object(PLUGIN.random, "choice", return_value=other) as choice:
            selected = PLUGIN.SetuPlugin._select_random_setu([duplicate, duplicate.copy(), other])

        self.assertIs(selected, other)
        self.assertEqual(choice.call_args.args[0], [duplicate, other])

    def test_select_random_setu_keeps_different_pages_of_same_work(self):
        first_page = _item(100, 0, "第一页")
        second_page = _item(100, 1, "第二页")

        with patch.object(PLUGIN.random, "choice", return_value=second_page) as choice:
            selected = PLUGIN.SetuPlugin._select_random_setu([first_page, second_page])

        self.assertIs(selected, second_page)
        self.assertEqual(choice.call_args.args[0], [first_page, second_page])


class HandlerSamplingTest(IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = object.__new__(PLUGIN.SetuPlugin)
        self.plugin._rate_limiter = SimpleNamespace(is_allowed=Mock(return_value=True), remaining=Mock(return_value=2))
        self.plugin.ctx = SimpleNamespace(
            logger=Mock(),
            send=SimpleNamespace(text=AsyncMock()),
        )

    async def test_tool_sends_and_reports_only_selected_candidate(self):
        first = _item(100, 0, "第一张")
        selected = _item(200, 0, "选中张")
        self.plugin._fetch_setu = AsyncMock(return_value={"success": True, "data": [first, selected]})
        self.plugin._send_setu_images = AsyncMock(return_value=(1, []))

        with patch.object(PLUGIN.random, "choice", return_value=selected):
            result = await self.plugin.handle_get_setu(tags=["角色"], num=3, stream_id="stream")

        self.plugin._fetch_setu.assert_awaited_once_with(tags=["角色"], num=3, r18=0)
        self.plugin._send_setu_images.assert_awaited_once_with([selected], "stream", group_id="", user_id="")
        self.assertIn("选中张", result["content"])
        self.assertNotIn("第一张", result["content"])
        self.assertIn("已发送 1 张图片", result["content"])

    async def test_tu_requests_three_candidates_and_sends_one(self):
        first = _item(100, 0, "第一张")
        selected = _item(200, 0, "选中张")
        self.plugin._fetch_setu = AsyncMock(return_value={"success": True, "data": [first, selected]})
        self.plugin._send_setu_images = AsyncMock(return_value=(1, []))

        with patch.object(PLUGIN.random, "choice", return_value=selected):
            result = await self.plugin.handle_tu_command(
                stream_id="stream",
                message={"plain_text": "/tu 角色"},
            )

        self.plugin._fetch_setu.assert_awaited_once_with(tags=["角色"], num=3, r18=0)
        self.plugin._send_setu_images.assert_awaited_once_with([selected], "stream", group_id="", user_id="")
        self.assertEqual(result, (True, "已发送 1 张图片", True))


if __name__ == "__main__":
    import unittest

    unittest.main()
