import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import engine, main, settlement_engine, writer


class SpreadsheetCreationTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_report_spreadsheet_surfaces_feishu_api_error(self):
        response = {"code": 99991663, "msg": "tenant access token invalid"}

        with patch.object(
            writer.feishu,
            "sheets_create",
            new=AsyncMock(return_value=response),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                r"创建飞书毛利报表失败.*99991663.*tenant access token invalid",
            ):
                await writer.create_report_spreadsheet("2026-07")

    async def test_final_product_sheets_do_not_receive_legacy_27_column_rows(self):
        sheet_map = {
            "10_毛利结果表": "legacy_result",
            "产品毛利_月度": "final_monthly",
            "产品毛利_季度": "final_quarterly",
            "11_店铺汇总看板": "shop_summary",
            "12_异常预警": "alerts",
        }
        result = {
            "by_sku": {},
            "shop_totals": {},
            "shop_log_stat": {},
        }

        with patch.object(writer, "_batch_write", new=AsyncMock()) as batch_write:
            await writer.write_result_sheets("sheet_token", sheet_map, "2026-07", result)

        written_sheet_ids = [call.args[1] for call in batch_write.await_args_list]
        self.assertNotIn("final_monthly", written_sheet_ids)
        self.assertNotIn("final_quarterly", written_sheet_ids)

    def test_final_product_headers_use_unified_18_column_contract(self):
        expected = [
            "国内电商平台名称", "运营人员", "国家", "站点", "月份", "MSKU", "中文名称",
            "销量", "退款数量", "销售额(RMB)", "退款(RMB)", "平台服务费(RMB)",
            "广告费(RMB)", "采购成本(RMB)", "尾程费用(RMB)", "其他成本(RMB)",
            "毛利润(RMB)", "毛利率",
        ]
        self.assertEqual(expected, writer.HEADERS["产品毛利_月度"])
        self.assertEqual(expected, writer.HEADERS["产品毛利_季度"])
        sheet_columns = {name: columns for name, _, columns in writer.SHEET_DEFS}
        self.assertEqual(len(expected), sheet_columns["产品毛利_月度"])
        self.assertEqual(len(expected), sheet_columns["产品毛利_季度"])

    def test_settlement_product_rows_match_unified_18_column_contract(self):
        report = settlement_engine.SettlementReport("2026-07")
        report.add_product(
            "抖音", "抖音宝空", "PK02-S3", "食人花2代", 1, 0.1,
            552, 52, 11.04, 0, 170.26, 26, 4.57,
        )
        rows = report.product_rows()

        self.assertEqual(18, len(settlement_engine.PRODUCT_HEADER))
        self.assertEqual(18, len(rows[0]))
        self.assertAlmostEqual(rows[0][16], 288.13, places=2)
        self.assertAlmostEqual(rows[0][17], 288.13 / (552 - 52), places=6)

    def test_unmapped_sku_rows_are_not_merged_together(self):
        report = settlement_engine.SettlementReport("2026-07")
        for sales in (100, 200):
            report.add_product(
                "抖音", "抖音宝空", "", "ERP品名未维护（ERP SKU未映射）",
                1, 0, sales, 0, 0, 0, 0, 0, 0,
            )

        self.assertEqual(2, len(report.product_rows()))

    async def test_health_version_matches_fastapi_version(self):
        payload = await main.health()

        self.assertEqual(main.app.version, payload["version"])
        self.assertTrue(payload["tmall_ad_actual_consumption_only"])
        self.assertTrue(payload["douyin_ad_evidence_gate"])

    def test_legacy_result_does_not_fall_back_to_marketplace_title(self):
        result = engine.compute(
            orders=[{
                "platform": "天猫", "shop": "天猫宝空", "sku": "SKU-X",
                "title": "平台商品长标题", "qty": 1, "payable": 100,
                "main_oid": "order-x", "status": "交易成功",
            }],
            refunds=[], plat_fees=[], ads=[], logistics=[],
            sku_costs={}, sku_names={}, year_month="2026-07",
        )

        row = result["by_sku"][("天猫", "天猫宝空", "SKU-X")]
        self.assertEqual("ERP品名未维护（SKU-X）", row["name"])
        self.assertNotIn("平台商品长标题", row["name"])


if __name__ == "__main__":
    unittest.main()
