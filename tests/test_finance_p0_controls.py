import inspect
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import card_workflow, company_profit, main


class FinanceRouteContractTests(unittest.TestCase):
    def test_production_endpoint_does_not_default_to_frankie_private_chat(self):
        default = inspect.signature(main.cards_finance_confirm).parameters["frankie_only"].default
        self.assertIs(False, default)

    def test_generic_finance_decisions_match_existing_base_select_options(self):
        self.assertEqual({
            "accept_temp": "接受临时估算",
            "return_data": "退回资料缺口",
            "return_method": "退回口径问题",
            "pending": "待确认",
        }, card_workflow.OUTPUT_GENERIC_DECISIONS)

    def test_company_total_requires_both_aggregate_and_archive_terminal_states(self):
        result = company_profit._verified_aggregate_result({
            "run": {"报表状态": "已归档", "总表状态": "已灌总表"},
        })
        self.assertTrue(result["archived"])

    def test_callback_success_without_company_total_terminal_state_is_not_enough(self):
        result = company_profit._verified_aggregate_result({
            "run": {"报表状态": "财务通过", "总表状态": "待灌总表"},
        })
        self.assertFalse(result["archived"])


class FinanceP0ControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_check_endpoint_is_attachment_only_and_silent(self):
        with patch.object(
            main.task_runner,
            "run_profit",
            new=AsyncMock(return_value={"ok": True, "mode": "initial_check_only"}),
        ) as run:
            result = await main.rerun_initial_check(
                main.RunRequest(record_id="rec_2026_07"),
                f"Bearer {main.config.WEBHOOK_BEARER_TOKEN}",
            )

        self.assertEqual("initial_check_only", result["mode"])
        run.assert_awaited_once_with(
            "rec_2026_07",
            suppress_notify=True,
            initial_check_only=True,
        )

    async def test_invalidation_patches_card_without_writing_new_select_option(self):
        run_id = "domestic-ecom-profit-2026-07"
        workbook = "https://u1wpma3xuhr.feishu.cn/sheets/old"
        output_id = card_workflow.ledger.output_id_for(run_id, workbook, "抖音")
        output = {
            "record_id": "rec_old",
            "fields": {
                "run_id": run_id,
                "workbook链接": workbook,
                "output_id": output_id,
                "确认卡message_id": "om_old",
                "财务决定": "待确认",
            },
        }
        with patch.object(
            card_workflow.ledger,
            "find_many",
            new=AsyncMock(return_value=[output]),
        ), patch.object(
            card_workflow.feishu,
            "patch_message_card",
            new=AsyncMock(return_value={"code": 0}),
        ), patch.object(
            card_workflow,
            "is_current_finance_output",
            new=AsyncMock(return_value=True),
        ), patch.object(
            card_workflow.ledger,
            "update",
            new=AsyncMock(),
        ) as update:
            result = await card_workflow.invalidate_finance_cards_for_run(
                run_id,
                reason="本卡无效，请勿操作",
                include_current=True,
            )

        self.assertEqual(1, result["invalidated_count"])
        update.assert_awaited_once_with(
            card_workflow.ledger.OUTPUT_TABLE,
            "rec_old",
            {"确认卡message_id": ""},
        )
        self.assertNotIn("财务决定", update.await_args.args[2])

    async def test_open_p0_blocks_finance_card_before_any_send(self):
        run = {"record_id": "rec_run", "fields": {"run_id": "domestic-ecom-profit-2026-07", "期间": "2026-07"}}
        output = {"record_id": "rec_out", "fields": {"run_id": "domestic-ecom-profit-2026-07", "output_id": "out_current"}}
        with patch.object(card_workflow.ledger, "find_first", new=AsyncMock(return_value=run)), patch.object(
            card_workflow.ledger, "open_p0_gaps", new=AsyncMock(return_value=[{"fields": {"gap_id": "gap_1"}}])
        ), patch.object(card_workflow, "_send_finance_confirm_card", new=AsyncMock()) as send:
            result = await card_workflow.send_finance_card(
                "domestic-ecom-profit-2026-07", output, platform="抖音", grant_access=False
            )

        self.assertEqual("open_p0", result["error"])
        send.assert_not_awaited()

    async def test_old_report_button_is_rejected_and_original_card_is_patched(self):
        body = {
            "event": {
                "action": {"value": {
                    "action": "domestic_profit_finance_approve",
                    "run_id": "domestic-ecom-profit-2026-07",
                    "period": "2026-07",
                    "platform": "抖音",
                    "output_id": "out_old",
                    "idempotency_key": "old-output-click",
                }},
                "context": {"open_message_id": "om_old", "open_chat_id": "oc_fin"},
                "operator": {"open_id": "ou_finance"},
            }
        }
        old = {"record_id": "rec_old", "fields": {"output_id": "out_old", "run_id": "domestic-ecom-profit-2026-07"}}
        with patch.object(card_workflow.ledger, "audit_exists", new=AsyncMock(return_value=False)), patch.object(
            card_workflow.ledger, "write_audit", new=AsyncMock()
        ), patch.object(card_workflow.ledger, "finalize_audit", new=AsyncMock()), patch.object(
            card_workflow.ledger, "find_first", new=AsyncMock(return_value=old)
        ), patch.object(card_workflow, "is_current_finance_output", new=AsyncMock(return_value=False)), patch.object(
            card_workflow.ledger, "update", new=AsyncMock()
        ) as update, patch.object(card_workflow, "_patch_or_reply", new=AsyncMock(return_value={"patched_original_card": True})):
            result = await card_workflow.handle_callback(body)

        self.assertFalse(result["ok"])
        self.assertEqual("superseded_output", result["error"])
        update.assert_not_awaited()

    async def test_all_platform_approval_archives_only_after_company_aggregate_success(self):
        result = await card_workflow.finalize_after_platform_approvals(
            "domestic-ecom-profit-2026-07",
            "2026-07",
            "out_current",
            "ou_finance",
            aggregate=AsyncMock(return_value={"ok": True, "aggregate_result": {"ok": True, "archived": True}}),
        )
        self.assertEqual("已归档", result["run_status"])

    async def test_failed_company_aggregate_never_archives(self):
        result = await card_workflow.finalize_after_platform_approvals(
            "domestic-ecom-profit-2026-07",
            "2026-07",
            "out_current",
            "ou_finance",
            aggregate=AsyncMock(return_value={"ok": False, "reason": "总表写入失败"}),
        )
        self.assertEqual("汇总失败待处理", result["run_status"])


if __name__ == "__main__":
    unittest.main()
