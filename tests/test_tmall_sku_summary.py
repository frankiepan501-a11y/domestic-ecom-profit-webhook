import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import settlement_engine as se
from app import writer


class TmallSkuSummaryTests(unittest.TestCase):
    def test_cent_allocation_reconciles_monthly_and_preserves_cost_source(self):
        report = se.SettlementReport("2026-08")
        report.add_product("天猫", "天猫宝空", "SKU-A", "产品A", 1, 0, 2, 0, 0, 0, 1, 0, 0)
        report.add_product("天猫", "天猫宝空", "SKU-B", "产品B", 1, 0, 1, 0, 0, 0, 1, 0, 0)
        report.add_cost("天猫", "天猫宝空", "ORDER-A", "SKU-A", "产品A", 1, 1, 1, "财务核算")
        report.add_cost("天猫", "天猫宝空", "ORDER-B", "SKU-B", "产品B", 1, 1, 1, "财务核算")
        report.add_monthly("天猫", "天猫宝空", 2, 2, 3, 0, .01, .02, 2, 0, 0, "试算")
        rows = report.sku_summary_rows()
        by_sku = {row[2]: row for row in rows}
        self.assertEqual(27, len(se.SKU_SUMMARY_HEADER))
        self.assertEqual(["SKU-A", "SKU-B", "店铺合计"], [row[2] for row in rows])
        self.assertEqual(.01, by_sku["SKU-A"][10])
        self.assertEqual(0, by_sku["SKU-B"][10])
        self.assertEqual(.01, by_sku["SKU-A"][11])
        self.assertEqual(.01, by_sku["SKU-B"][11])
        self.assertEqual(.01, by_sku["店铺合计"][10])
        self.assertEqual(.02, by_sku["店铺合计"][11])
        self.assertEqual("财务核算", by_sku["SKU-A"][25])

    def test_sheet_formulas_exclude_subtotals_from_allocation_weight(self):
        report = se.SettlementReport("2026-08")
        report.add_product("天猫", "天猫宝空", "SKU-A", "产品A", 1, 0, 100, 0, 0, 0, 10, 5, 0)
        report.add_cost("天猫", "天猫宝空", "ORDER-A", "SKU-A", "产品A", 1, 10, 10, "财务核算")
        report.add_monthly("天猫", "天猫宝空", 1, 1, 100, 0, 0, 0, 10, 5, 0, "试算")
        data = report.as_dict()
        sheet_rows = writer._sku_summary_sheet_rows(data["sku_summary_rows"])
        self.assertIn(("SKU毛利汇总", 1000, 27), writer.SHEET_DEFS)
        self.assertEqual(se.SKU_SUMMARY_HEADER, writer.HEADERS["SKU毛利汇总"])
        self.assertEqual("=H2-I2", sheet_rows[0][9]["text"])
        self.assertEqual("=J2-SUM(K2:O2)", sheet_rows[0][15]["text"])
        self.assertIn('"<>店铺合计"', sheet_rows[0][26]["text"])
        self.assertEqual("=SUM(H2:H2)", sheet_rows[1][7]["text"])
        self.assertEqual("=SUM(AA2:AA2)", sheet_rows[1][26]["text"])
        self.assertEqual("=J3-SUM(K3:O3)", sheet_rows[1][15]["text"])



class TmallSkuSheetWriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_writer_routes_27_column_summary_to_its_own_sheet(self):
        from unittest.mock import AsyncMock, patch
        report = se.SettlementReport("2026-08")
        report.add_product("天猫", "天猫宝空", "SKU-A", "产品A", 1, 0, 100, 0, 0, 0, 10, 5, 0)
        report.add_cost("天猫", "天猫宝空", "ORDER-A", "SKU-A", "产品A", 1, 10, 10, "财务核算")
        report.add_monthly("天猫", "天猫宝空", 1, 1, 100, 0, 0, 0, 10, 5, 0, "试算")
        with patch.object(writer.feishu, "sheets_values_batch_update", new=AsyncMock()) as headers, patch.object(
            writer, "_batch_write", new=AsyncMock()
        ) as write:
            await writer.write_settlement_sheets("token", {"SKU毛利汇总": "sku_sheet"}, report.as_dict())
        self.assertEqual("sku_sheet!A1:AA1", headers.await_args.args[1][0]["range"])
        self.assertEqual("sku_sheet", write.await_args.args[1])
        self.assertEqual(27, len(write.await_args.args[2][0]))

if __name__ == "__main__":
    unittest.main()
