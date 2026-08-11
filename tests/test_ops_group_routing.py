import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import card_workflow, cards, config


class CardMentionTests(unittest.TestCase):
    def test_monthly_intake_card_mentions_current_owner(self):
        card = cards.operation_submit_card(
            "domestic-ecom-profit-2026-07",
            "2026-07",
            ["拼多多/正方体电玩店 - 订单明细"],
            mention_open_ids=["ou_event_zhao"],
        )

        content = "\n".join(
            element.get("text", {}).get("content", "")
            for element in card["elements"]
            if element.get("tag") == "div"
        )
        self.assertIn("<at id=ou_event_zhao></at>", content)
        self.assertIn("请及时跟进", content)

    def test_gap_card_mentions_current_owner(self):
        card = cards.p0_gap_card(
            {
                "fields": {
                    "run_id": "domestic-ecom-profit-2026-07",
                    "gap_id": "gap_test",
                    "月份": "2026-07",
                    "平台": "拼多多",
                    "缺口类型": "其他",
                    "证据": "缺少订单明细",
                }
            },
            mention_open_ids=["ou_event_zhao"],
        )

        content = "\n".join(
            element.get("text", {}).get("content", "")
            for element in card["elements"]
            if element.get("tag") == "div"
        )
        self.assertIn("<at id=ou_event_zhao></at>", content)
        self.assertIn("请及时跟进", content)


class OpsGroupRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_card_sends_once_to_configured_group(self):
        response = {"code": 0, "data": {"message_id": "om_group"}}
        with patch.object(
            card_workflow.feishu,
            "send_interactive_chat",
            new=AsyncMock(return_value=response),
        ) as send_group, patch.object(
            card_workflow,
            "_send_card_to_union_targets",
            new=AsyncMock(),
        ) as send_private:
            sent, targets, route = await card_workflow._send_ops_card(
                {"elements": []},
                frankie_only=False,
            )

        self.assertEqual(["om_group"], sent)
        self.assertEqual([config.OPS_CARD_CHAT_NAME], targets)
        self.assertEqual("group", route)
        send_group.assert_awaited_once_with(
            config.OPS_CARD_CHAT_ID,
            {"elements": []},
            use_event_app=True,
        )
        send_private.assert_not_awaited()

    async def test_frankie_only_card_stays_private(self):
        private_targets = {config.FRANKIE_UNION_ID: "潘志聪"}
        with patch.object(
            card_workflow,
            "_ops_union_targets",
            new=AsyncMock(return_value=private_targets),
        ), patch.object(
            card_workflow,
            "_send_card_to_union_targets",
            new=AsyncMock(return_value=["om_private"]),
        ) as send_private, patch.object(
            card_workflow.feishu,
            "send_interactive_chat",
            new=AsyncMock(),
        ) as send_group:
            sent, targets, route = await card_workflow._send_ops_card(
                {"elements": []},
                frankie_only=True,
            )

        self.assertEqual(["om_private"], sent)
        self.assertEqual(["潘志聪"], targets)
        self.assertEqual("private", route)
        send_private.assert_awaited_once()
        send_group.assert_not_awaited()

    async def test_mentions_use_exact_title_and_event_app_namespace(self):
        with patch.object(
            card_workflow.feishu,
            "resolve_users_by_job_title",
            new=AsyncMock(return_value={"ou_app1_zhao": "赵伟俊"}),
        ) as resolve_exact, patch.object(
            card_workflow.feishu,
            "resolve_users_jt_fallback",
            new=AsyncMock(side_effect=AssertionError("must not fall back to whole department")),
        ), patch.object(
            card_workflow.feishu,
            "open_id_to_union_id",
            new=AsyncMock(return_value="on_zhao"),
        ), patch.object(
            card_workflow.feishu,
            "chat_member_union_ids",
            new=AsyncMock(return_value={"on_zhao"}),
        ), patch.object(
            card_workflow.feishu,
            "contact_user_get_by_union_id",
            new=AsyncMock(return_value={"open_id": "ou_event_zhao", "name": "赵伟俊"}),
        ):
            mentions = await card_workflow._ops_group_mentions()

        self.assertEqual({"ou_event_zhao": "赵伟俊"}, mentions)
        resolve_exact.assert_awaited_once_with(
            config.REMIND_OPS_DEPT_ROOTS,
            config.OPS_CARD_MENTION_JOB_TITLES,
        )

    async def test_no_title_match_fails_without_department_wide_mention(self):
        with patch.object(
            card_workflow.feishu,
            "resolve_users_by_job_title",
            new=AsyncMock(return_value={}),
        ), patch.object(
            card_workflow.feishu,
            "resolve_users_jt_fallback",
            new=AsyncMock(side_effect=AssertionError("must not fall back to whole department")),
        ):
            with self.assertRaisesRegex(RuntimeError, "运营岗位"):
                await card_workflow._ops_group_mentions()

    async def test_force_resend_appends_group_message_to_old_private_message(self):
        gap = {
            "fields": {
                "run_id": "domestic-ecom-profit-2026-07",
                "gap_id": "gap_old_private",
                "月份": "2026-07",
                "平台": "拼多多",
                "缺口类型": "其他",
                "证据": "缺少订单明细",
                "message_id": "om_private_old",
            }
        }
        with patch.object(
            card_workflow.ledger,
            "open_p0_gaps",
            new=AsyncMock(return_value=[gap]),
        ), patch.object(
            card_workflow,
            "_ops_group_mentions",
            new=AsyncMock(return_value={"ou_event_zhao": "赵伟俊"}),
        ), patch.object(
            card_workflow,
            "_send_ops_card",
            new=AsyncMock(return_value=(["om_group_new"], ["国内电商平台沟通群"], "group")),
        ), patch.object(
            card_workflow.ledger,
            "mark_gap",
            new=AsyncMock(),
        ) as mark_gap:
            sent = await card_workflow.send_open_gap_cards(
                "domestic-ecom-profit-2026-07",
                force_resend=True,
            )

        self.assertEqual(["om_group_new"], sent)
        mark_gap.assert_awaited_once_with(
            "gap_old_private",
            {"message_id": "om_private_old,om_group_new"},
        )

    async def test_existing_group_message_skips_without_resolving_mentions(self):
        gap = {"fields": {"message_id": "om_group_existing"}}
        with patch.object(
            card_workflow.ledger,
            "open_p0_gaps",
            new=AsyncMock(return_value=[gap]),
        ), patch.object(
            card_workflow,
            "_ops_group_mentions",
            new=AsyncMock(side_effect=AssertionError("no send means no recipient lookup")),
        ):
            sent = await card_workflow.send_open_gap_cards("run_done")

        self.assertEqual([], sent)


if __name__ == "__main__":
    unittest.main()
