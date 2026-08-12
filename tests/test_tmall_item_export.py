import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import settlement_engine


class TmallItemExportTests(unittest.TestCase):
    def test_item_export_supplies_merchant_sku_for_settlement_order(self):
        raw = {
            "source_files": [
                {
                    "platform": "天猫",
                    "shop": "POWKONG旗舰店",
                    "fname": "交易货款_202607_202607.csv",
                    "buf": (
                        "子订单号,订单号,数量,订单实际金额（元）,退款金额（元）,商品名称\n"
                        "5113837045333047045,5113837045333047045,1,99,0,食人花2代\n"
                    ).encode("utf-8"),
                },
                {
                    "platform": "天猫",
                    "shop": "POWKONG旗舰店",
                    "fname": "ExportItemlList202608111558.csv",
                    "buf": (
                        "子订单编号,主订单编号,商家编码,外部系统编号,商品标题,物流单号,物流公司\n"
                        "5113837045333047045,5113837045333047045,PK02-S2,商品规格,食人花2代,SF123,顺丰速运\n"
                    ).encode("utf-8"),
                },
            ],
            "logistics": [],
            "sku_set": set(),
        }
        cost_map = {
            "PK02-S2": {
                "unit_cost": 10,
                "source": "产品采购成本台",
                "name": "食人花2代",
            }
        }

        result = settlement_engine.compute(raw, cost_map, "2026-07")
        extracted_skus = settlement_engine.extract_skus(raw, "2026-07")

        matching_costs = [
            row for row in result["cost_rows"]
            if row[3] == "5113837045333047045"
        ]
        matching_gaps = [
            row for row in result["gap_rows"]
            if row[5] == "5113837045333047045"
        ]
        self.assertEqual("PK02-S2", matching_costs[0][4])
        self.assertIn("PK02-S2", extracted_skus)
        self.assertFalse(any("商家编码" in str(row[6]) for row in matching_gaps))


if __name__ == "__main__":
    unittest.main()
