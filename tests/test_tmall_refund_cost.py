import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import settlement_engine as se
from app import parsers
from app import task_runner

class TmallRefundCostTests(unittest.TestCase):
    def test_official_refund_export_exposes_cost_evidence_fields(self):
        rows = [[
            "退款编号", "订单编号", "退款总额", "商家编码", "退款状态",
            "部分退款/全额退款", "货物状态", "退货物流信息",
        ], [
            "R1", "5127690493456008025", 449, "PJ005", "退款成功",
            "全额退款", "已寄回", "已签收",
        ]]
        with patch.object(parsers, "_load_rows", return_value=rows):
            parsed = parsers.parse_tmall_refunds(b"x", "refund.xlsx")
        self.assertEqual("全额退款", parsed[0]["refund_scope"])
        self.assertEqual("已寄回", parsed[0]["goods_status"])
        self.assertEqual("已签收", parsed[0]["return_logistics"])

    def test_full_refund_evidence_map_ignores_partial_refund_row(self):
        raw = {"tmall_refund_scope": {}, "tmall_cost_return_status": {}}
        rows = [
            {"main_oid": "3316365734588005971", "refund_scope": "全额退款",
             "status": "退款成功", "goods_status": "已寄回", "return_logistics": "已签收"},
            {"main_oid": "3316365734588005971", "refund_scope": "部分退款",
             "status": "退款成功", "goods_status": "", "return_logistics": ""},
        ]
        task_runner._merge_tmall_refund_cost_evidence(raw, rows)
        self.assertEqual("全额退款", raw["tmall_refund_scope"]["3316365734588005971"])
        self.assertEqual("已退回签收", raw["tmall_cost_return_status"]["3316365734588005971"])

    def test_full_refund_reverses_unit_partial_refund_keeps_unit(self):
        sales=[
            {'子订单号':'FULL','订单号':'FULL','数量':'1','订单实际金额（元）':'100','退款金额（元）':'0'},
            {'子订单号':'FULL','订单号':'FULL','数量':'1','订单实际金额（元）':'0','退款金额（元）':'90'},
            {'子订单号':'PART','订单号':'PART','数量':'1','订单实际金额（元）':'100','退款金额（元）':'0'},
            {'子订单号':'PART','订单号':'PART','数量':'1','订单实际金额（元）':'0','退款金额（元）':'20'},
        ]
        orders=[
            {'子订单编号':'FULL','商家编码':'SKU-A','物流单号':'WB-FULL','物流公司':'顺丰'},
            {'子订单编号':'PART','商家编码':'SKU-A','物流单号':'WB-PART','物流公司':'顺丰'},
        ]
        files=[{'platform':'天猫','shop':'天猫宝空','fname':'交易货款.csv','buf':b'settlement'}, {'platform':'天猫','shop':'天猫宝空','fname':'8月订单详细.xlsx','buf':b'orders'}]
        report=se.SettlementReport('2026-08')
        cost_map={'SKU-A':{'unit_cost':30,'name':'产品A','source':'财务核算'}}
        bills={'WB-FULL':{'amount':5,'file':'bill','sheet':'data','source':'账单'},'WB-PART':{'amount':5,'file':'bill','sheet':'data','source':'账单'}}
        def fake_sheet_rows(buf,*args,**kwargs): return orders
        with patch.object(se,'read_csv',return_value=sales), patch.object(se,'sheet_rows',side_effect=fake_sheet_rows), patch.object(se,'read_tmall_fees',return_value=(0,0)), patch.object(se,'build_bill_pool',return_value=bills):
            raw = {"source_files": files, "tmall_refund_scope": {}, "tmall_cost_return_status": {}}
            task_runner._merge_tmall_refund_cost_evidence(raw, [{
                "main_oid": "FULL", "refund_scope": "全额退款",
                "status": "退款成功", "goods_status": "已寄回",
                "return_logistics": "已签收",
            }])
            se.process_tmall(report, raw, cost_map, bills, "天猫宝空")
        self.assertEqual(2,len(report.cost_rows))
        costs={r[3]:r for r in report.cost_rows}
        self.assertEqual(0,costs['FULL'][6])
        self.assertEqual(1,costs['PART'][6])
        self.assertEqual(30,report.monthly[0][11])
        self.assertEqual(2,report.monthly[0][4])
        self.assertEqual(2,report.monthly[0][5])
        self.assertEqual(90,report.monthly[0][8])
        sku=report.sku_summary_rows()[0]
        self.assertEqual(1.1,sku[5])
        self.assertEqual(1,sku[6])
        self.assertEqual(30,sku[12])
        self.assertEqual(0,report.gap_count('天猫','天猫宝空','P0'))

        unknown=se.SettlementReport('2026-08')
        with patch.object(se,'read_csv',return_value=sales), patch.object(se,'sheet_rows',side_effect=fake_sheet_rows), patch.object(se,'read_tmall_fees',return_value=(0,0)), patch.object(se,'build_bill_pool',return_value=bills):
            se.process_tmall(unknown,{'source_files':files,'tmall_refund_scope':{'FULL':'全额退款'}},cost_map,bills,'天猫宝空')
        self.assertEqual(1,unknown.gap_count('天猫','天猫宝空','P0'))
        self.assertEqual(60,unknown.monthly[0][11])

if __name__=='__main__': unittest.main()
