# -*- coding: utf-8 -*-
"""UU SDK（api.uuyoupinapi.UUAccount）sell_items 的回归测试。

重点覆盖 2026-09-13 修复的 bug：UU 上架接口返回二次确认场景（code=7000002，
响应只有 errorData 无 Data）时，旧实现 ``rsp["Data"]`` 直接抛
``KeyError: 'Data'``（CLI 显示「错误：'Data'」，用户无法定位原因）。

测试完全离线：``object.__new__`` 绕过 ``__init__``（其会联网调 getUserInfo），
mock ``call_api`` 注入伪造响应。
"""

import os
import sys
import unittest

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from api.uuyoupinapi import UUAccount  # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _SellItemsTestBase(unittest.TestCase):
    def _client(self, payload):
        """构造绕过 __init__ 的 UUAccount，call_api 返回伪造响应。"""
        u = object.__new__(UUAccount)
        u.call_api = lambda *a, **k: _FakeResponse(payload)
        return u


class TestSellItemsSceneConfirm(_SellItemsTestBase):
    """code=7000002 二次确认场景：不再 KeyError，改抛带业务信息的 RuntimeError。"""

    PAYLOAD = {
        "msg": "成功",
        "code": 7000002,
        "errorData": {
            "sceneDesc": "由于Steam交易撤回机制，已成交的报价可在Steam中撤回……",
            "sceneCode": "RBFFD3EDBDF06402DA14C9052EA220D78",
            "sceneName": "出售场景交易撤回提示",
            "userConfirms": "我已经知道上述情况，继续操作",
            "countdown": 5,
            "content": [
                {"isRequired": 1, "checkLabel": "我已知晓", "oneLevelContent": "发货后，请勿在steam撤回交易"},
                {"isRequired": 1, "checkLabel": "我已知晓", "oneLevelContent": "如果买家撤回报价"},
            ],
            "sceneTitle": "交易撤回须知",
        },
    }

    def test_raises_runtime_error_not_keyerror(self):
        u = self._client(self.PAYLOAD)
        with self.assertRaises(RuntimeError) as ctx:
            u.sell_items({"50687781534": 1.0})
        msg = str(ctx.exception)
        # 必须是可读的业务信息，而不是 KeyError('Data')
        self.assertIn("交易撤回须知", msg)
        self.assertIn("7000002", msg)
        self.assertIn("我已经知道上述情况，继续操作", msg)
        self.assertIn("发货后，请勿在steam撤回交易", msg)
        self.assertIn("APP", msg, "应提示场景确认需 APP 完成")

    def test_keyerror_not_raised(self):
        """回归保护：确保不会再抛 KeyError（旧 bug 的表现形式）。"""
        u = self._client(self.PAYLOAD)
        try:
            u.sell_items({"1": 1.0})
        except RuntimeError:
            pass  # 预期路径
        except KeyError as e:  # pragma: no cover - 修复后不应到达
            self.fail("sell_items 仍在抛 KeyError（旧 bug 未修复）：%r" % (e,))

    def test_scene_confirm_without_errordata_fields(self):
        """errorData 缺字段时也要给清晰错误，不能 KeyError。"""
        u = self._client({"msg": "成功", "code": 7000002, "errorData": {}})
        with self.assertRaises(RuntimeError) as ctx:
            u.sell_items({"1": 1.0})
        self.assertIn("二次确认场景", str(ctx.exception))


class TestSellItemsOtherBusinessError(_SellItemsTestBase):
    def test_generic_nonzero_code(self):
        u = self._client({"code": 84104, "msg": "操作太频繁"})
        with self.assertRaises(RuntimeError) as ctx:
            u.sell_items({"1": 1.0})
        msg = str(ctx.exception)
        self.assertIn("84104", msg)
        self.assertIn("操作太频繁", msg)

    def test_code_zero_but_no_data_field(self):
        """code=0 但响应结构异常（无 Data）：也要抛清晰错误而非 KeyError。"""
        u = self._client({"code": 0, "msg": "成功"})
        with self.assertRaises(RuntimeError):
            u.sell_items({"1": 1.0})


class TestSellItemsSuccess(_SellItemsTestBase):
    def test_all_success(self):
        payload = {
            "code": 0,
            "msg": "成功",
            "Data": [
                {"AssetId": "50687781534", "Status": 1, "Remark": ""},
                {"AssetId": "50687779046", "Status": 1, "Remark": ""},
            ],
        }
        u = self._client(payload)
        result = u.sell_items({"50687781534": 1.0, "50687779046": 1.0})
        self.assertEqual(result, {"success": 2, "total": 2, "problems": {}})

    def test_partial_failure_collects_problems(self):
        payload = {
            "code": 0,
            "Data": [
                {"AssetId": "1", "Status": 1, "Remark": ""},
                {"AssetId": "2", "Status": 0, "Remark": "价格低于平台最低价"},
            ],
        }
        u = self._client(payload)
        result = u.sell_items({"1": 1.0, "2": 0.01})
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["problems"], {"2": "价格低于平台最低价"})

    def test_duplicate_listing_is_problem_not_crash(self):
        """「不能重复上架」旧实现访问 commodity['Remark'] 用下标，Remark 缺失会 KeyError。"""
        payload = {"code": 0, "Data": [{"AssetId": "1", "Status": 0}]}
        u = self._client(payload)
        result = u.sell_items({"1": 1.0})
        self.assertEqual(result["success"], 0)
        self.assertEqual(result["problems"], {"1": "未知原因"})

    def test_list_input_full_item_infos(self):
        """list 入参（插件用完整 ItemInfos 字段）也应正常工作。"""
        payload = {
            "code": 0,
            "Data": [
                {"AssetId": "50687781534", "Status": 1, "Remark": ""},
            ],
        }
        u = self._client(payload)
        full_items = [
            {"AssetId": "50687781534", "IsCanLease": False, "IsCanSold": True, "Price": 1.0, "Remark": ""},
        ]
        result = u.sell_items(full_items)
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["problems"], {})

    def test_invalid_input_raises_type_error(self):
        u = self._client({"code": 0, "Data": []})
        with self.assertRaises(TypeError):
            u.sell_items("not-a-dict-or-list")


if __name__ == "__main__":
    unittest.main()
