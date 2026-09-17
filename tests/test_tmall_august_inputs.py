import collections
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import settlement_engine as se


class TmallAugustInputTests(unittest.TestCase):
    def test_signed_fee_refund_and_duplicate_file(self):
        body = "订单号,支出金额（元）\nA,20.00\nA,-3.50\n".encode("utf-8-sig")
        report = se.SettlementReport("2026-08")
        by_order = collections.defaultdict(float)
        fee, ad = se.read_tmall_fees(
            report,
            [
                {"fname": "8月宝空类目软件服务费.csv", "buf": body},
                {"fname": "duplicate/8月宝空类目软件服务费.csv", "buf": body},
            ],
            "天猫宝空",
            by_order,
        )
        self.assertEqual((16.5, 0.0), (fee, ad))
        self.assertEqual(16.5, by_order["A"])

    def test_august_carrier_bill_names(self):
        import io
        import openpyxl
        sf = openpyxl.Workbook()
        ws = sf.active
        ws.title = "账单明细"
        ws.append(["说明"])
        ws.append(["日期", "其他", "运单号码", "应付金额"])
        ws.append(["2026-08-01", "", "SF123", 20])
        out = io.BytesIO()
        sf.save(out)
        sf.close()
        sf_bytes = out.getvalue()

        zto = openpyxl.Workbook()
        ws = zto.active
        ws.title = "Sheet1"
        ws.append(["账单日期", "运单号", "合计"])
        ws.append(["2026-08-01", "ZT123", 8])
        out = io.BytesIO()
        zto.save(out)
        zto.close()
        pool = se.build_bill_pool({"source_files": [
            {"fname": "760BC_7604967054-202608.xlsx", "buf": sf_bytes},
            {"fname": "duplicate/760BC_7604967054-202608.xlsx", "buf": sf_bytes},
            {"fname": "FUNLAB纷岚的店 8月份账单.xlsx", "buf": out.getvalue()},
        ]})
        self.assertEqual(20, pool["SF123"]["amount"])
        self.assertEqual(8, pool["ZT123"]["amount"])
    def test_august_ad_and_order_export_names(self):
        self.assertTrue(se._is_order_detail_export("8月宝空订单详细.xlsx"))
        body = (
            "交易日期,交易类型,操作金额(元)\n"
            "2026-08-01,扣款,10\n"
            "2026-08-02,充值,100\n"
            "2026-07-31,扣款,5\n"
        ).encode("gbk")
        fee, ad = se.read_tmall_fees(
            se.SettlementReport("2026-08"),
            [{"fname": "8月宝空广告账单.csv", "buf": body}],
            "天猫宝空",
            collections.defaultdict(float),
        )
        self.assertEqual((0.0, 10.0), (fee, ad))


if __name__ == "__main__":
    unittest.main()
