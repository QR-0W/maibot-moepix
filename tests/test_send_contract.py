from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import asyncio

from test_sampling import _load_plugin_module, _item


PLUGIN = _load_plugin_module()


def _make_plugin():
    plugin = object.__new__(PLUGIN.SetuPlugin)
    plugin._rate_limiter = SimpleNamespace(is_allowed=Mock(return_value=True), remaining=Mock(return_value=2))
    plugin.config = SimpleNamespace(
        api=SimpleNamespace(send_mode="image"),
        limits=SimpleNamespace(recall_after_seconds=0),
    )
    plugin.ctx = SimpleNamespace(
        logger=Mock(),
        api=SimpleNamespace(call=AsyncMock()),
        send=SimpleNamespace(image=AsyncMock(return_value=True), text=AsyncMock(return_value=True)),
    )
    return plugin


def _nak_success(message_id=123456):
    """NapCat ACK: status=ok, retcode=0"""
    return {"status": "ok", "retcode": 0, "data": {"message_id": message_id}}


def _nak_success_no_msg_id():
    """NapCat ACK without message_id"""
    return {"status": "ok", "retcode": 0, "data": {}}


def _runtime_failure(error="NapCat 动作执行失败: action=send_msg error="):
    """Runtime wraps Adapter exception into failure dict"""
    return {"success": False, "error": error}


class SendImageContractTest(IsolatedAsyncioTestCase):
    """_send_image_via_api 的 confirmed/message_id/error 三要素契约"""

    def setUp(self):
        self.plugin = _make_plugin()

    async def test_confirmed_true_on_napcat_ack(self):
        self.plugin.ctx.api.call.return_value = _nak_success(1198417305)

        confirmed, msg_id, error = await self.plugin._send_image_via_api(
            "fake_b64", "stream", group_id="717566174"
        )

        self.assertTrue(confirmed)
        self.assertEqual(msg_id, 1198417305)
        self.assertIsNone(error)

    async def test_confirmed_true_on_missing_message_id(self):
        self.plugin.ctx.api.call.return_value = _nak_success_no_msg_id()

        confirmed, msg_id, error = await self.plugin._send_image_via_api(
            "fake_b64", "stream", group_id="717566174"
        )

        self.assertTrue(confirmed)
        self.assertIsNone(msg_id)
        self.assertIsNone(error)

    async def test_fallback_to_ctx_send_on_runtime_failure_dict(self):
        self.plugin.ctx.api.call.return_value = _runtime_failure()

        confirmed, msg_id, error = await self.plugin._send_image_via_api(
            "fake_b64", "stream", group_id="717566174"
        )

        self.assertTrue(confirmed)
        self.assertIsNone(msg_id)
        self.assertIsNone(error)
        self.plugin.ctx.send.image.assert_awaited_once()

    async def test_fallback_to_ctx_send_on_rpc_timeout(self):
        self.plugin.ctx.api.call.side_effect = asyncio.TimeoutError("send_msg timeout")

        confirmed, msg_id, error = await self.plugin._send_image_via_api(
            "fake_b64", "stream", group_id="717566174"
        )

        self.assertTrue(confirmed)
        self.assertIsNone(msg_id)
        self.assertIsNone(error)
        self.plugin.ctx.send.image.assert_awaited_once()

    async def test_confirmed_false_on_missing_target(self):
        confirmed, msg_id, error = await self.plugin._send_image_via_api(
            "fake_b64", "stream"
        )

        self.assertFalse(confirmed)
        self.assertIsNone(msg_id)
        self.assertIn("缺少发送目标", error)

    async def test_fallback_to_ctx_send_on_exception(self):
        self.plugin.ctx.api.call.side_effect = RuntimeError("boom")

        confirmed, msg_id, error = await self.plugin._send_image_via_api(
            "fake_b64", "stream", group_id="717566174"
        )

        self.assertTrue(confirmed)
        self.assertIsNone(msg_id)
        self.assertIsNone(error)
        self.plugin.ctx.send.image.assert_awaited_once()


class SendTextContractTest(IsolatedAsyncioTestCase):
    """_send_text_via_api 的 confirmed/message_id/error 三要素契约"""

    def setUp(self):
        self.plugin = _make_plugin()

    async def test_confirmed_true_on_napcat_ack(self):
        self.plugin.ctx.api.call.return_value = _nak_success(99999)

        confirmed, msg_id, error = await self.plugin._send_text_via_api(
            "hello", "stream", group_id="717566174"
        )

        self.assertTrue(confirmed)
        self.assertEqual(msg_id, 99999)
        self.assertIsNone(error)

    async def test_fallback_to_ctx_send_on_failure_dict(self):
        self.plugin.ctx.api.call.return_value = _runtime_failure()

        confirmed, msg_id, error = await self.plugin._send_text_via_api(
            "hello", "stream", group_id="717566174"
        )

        self.assertTrue(confirmed)
        self.assertIsNone(msg_id)
        self.assertIsNone(error)
        self.plugin.ctx.send.text.assert_awaited_once()

    async def test_fallback_to_ctx_send_on_exception(self):
        self.plugin.ctx.api.call.side_effect = RuntimeError("boom")

        confirmed, msg_id, error = await self.plugin._send_text_via_api(
            "hello", "stream", group_id="717566174"
        )

        self.assertTrue(confirmed)
        self.assertIsNone(msg_id)
        self.assertIsNone(error)
        self.plugin.ctx.send.text.assert_awaited_once()


class SendSetuImagesCountingTest(IsolatedAsyncioTestCase):
    """_send_setu_images 的计数行为：仅 confirmed 时增加 sent_count"""

    def setUp(self):
        self.plugin = _make_plugin()
        self.plugin._download_image_as_base64 = AsyncMock(return_value="fake_b64")

    async def test_sent_count_zero_when_send_fails(self):
        self.plugin._send_image_via_api = AsyncMock(
            return_value=(False, None, "NapCat 动作执行失败")
        )

        sent_count, info_lines = await self.plugin._send_setu_images(
            [_item(100, 0, "测试")], "stream", group_id="717566174"
        )

        self.assertEqual(sent_count, 0)
        self.assertTrue(any("NapCat 动作执行失败" in line for line in info_lines))

    async def test_sent_count_one_when_send_confirmed(self):
        self.plugin._send_image_via_api = AsyncMock(
            return_value=(True, 12345, None)
        )

        sent_count, _ = await self.plugin._send_setu_images(
            [_item(100, 0, "测试")], "stream", group_id="717566174"
        )

        self.assertEqual(sent_count, 1)

    async def test_sent_count_zero_when_download_fails(self):
        self.plugin._download_image_as_base64 = AsyncMock(return_value=None)

        sent_count, info_lines = await self.plugin._send_setu_images(
            [_item(100, 0, "测试")], "stream", group_id="717566174"
        )

        self.assertEqual(sent_count, 0)
        self.assertTrue(any("图片下载失败" in line for line in info_lines))

    async def test_link_mode_confirmed_counting(self):
        self.plugin.config = SimpleNamespace(
            api=SimpleNamespace(send_mode="link"),
            limits=SimpleNamespace(recall_after_seconds=0),
        )
        self.plugin._send_text_via_api = AsyncMock(
            return_value=(True, 777, None)
        )

        sent_count, _ = await self.plugin._send_setu_images(
            [_item(100, 0, "测试")], "stream", group_id="717566174"
        )

        self.assertEqual(sent_count, 1)

    async def test_link_mode_failure_not_counted(self):
        self.plugin.config = SimpleNamespace(
            api=SimpleNamespace(send_mode="link"),
            limits=SimpleNamespace(recall_after_seconds=0),
        )
        self.plugin._send_text_via_api = AsyncMock(
            return_value=(False, None, "发送失败")
        )

        sent_count, info_lines = await self.plugin._send_setu_images(
            [_item(100, 0, "测试")], "stream", group_id="717566174"
        )

        self.assertEqual(sent_count, 0)
        self.assertTrue(any("发送失败" in line for line in info_lines))


class HandlerWaitTest(IsolatedAsyncioTestCase):
    """get_setu 等待发送完成后才返回结果"""

    def setUp(self):
        self.plugin = _make_plugin()
        self.plugin._fetch_setu = AsyncMock(
            return_value={"success": True, "data": [_item(200, 0, "选中张")]}
        )
        self.plugin._select_random_setu = Mock(return_value=_item(200, 0, "选中张"))

    async def test_returns_sent_on_confirmed(self):
        self.plugin._send_setu_images = AsyncMock(return_value=(1, []))

        with patch.object(PLUGIN.random, "choice", return_value=_item(200, 0, "选中张")):
            result = await self.plugin.handle_get_setu(
                tags=["角色"], num=1, stream_id="stream"
            )

        self.plugin._send_setu_images.assert_awaited_once()
        self.assertIn("已发送 1 张图片", result["content"])

    async def test_returns_failure_on_send_failure(self):
        self.plugin._send_setu_images = AsyncMock(
            return_value=(0, ["• 测试 - 作者 - NapCat 动作执行失败"])
        )

        with patch.object(PLUGIN.random, "choice", return_value=_item(200, 0, "选中张")):
            result = await self.plugin.handle_get_setu(
                tags=["角色"], num=1, stream_id="stream"
            )

        self.plugin._send_setu_images.assert_awaited_once()
        self.assertNotIn("已发送", result["content"])
        self.assertIn("发送失败", result["content"])


class TuCommandContractTest(IsolatedAsyncioTestCase):
    """/tu 命令零成功返回失败"""

    def setUp(self):
        self.plugin = _make_plugin()
        self.plugin._fetch_setu = AsyncMock(
            return_value={"success": True, "data": [_item(200, 0, "选中张")]}
        )
        self.plugin._select_random_setu = Mock(return_value=_item(200, 0, "选中张"))

    async def test_returns_false_on_zero_sent(self):
        self.plugin._send_setu_images = AsyncMock(return_value=(0, ["失败"]))

        with patch.object(PLUGIN.random, "choice", return_value=_item(200, 0, "选中张")):
            success, msg, _ = await self.plugin.handle_tu_command(
                stream_id="stream",
                message={"plain_text": "/tu 角色"},
            )

        self.assertFalse(success)
        self.assertIn("发送失败", msg)

    async def test_returns_true_on_one_sent(self):
        self.plugin._send_setu_images = AsyncMock(return_value=(1, []))

        with patch.object(PLUGIN.random, "choice", return_value=_item(200, 0, "选中张")):
            success, msg, _ = await self.plugin.handle_tu_command(
                stream_id="stream",
                message={"plain_text": "/tu 角色"},
            )

        self.assertTrue(success)
        self.assertIn("已发送 1 张图片", msg)
