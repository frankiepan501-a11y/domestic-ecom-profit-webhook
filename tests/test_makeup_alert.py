import asyncio
import json
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import makeup_alert, settlement_engine


def official(*orders):
    return {('天猫纷岚', order): amount for order, amount in orders}


def row(order='3317044944002077875', plan=219, actual=169, received=0, **kwargs):
    fields = {'平台':'天猫','店铺':'纷岚旗舰店','订单号':order,
              '计划订单金额':plan,'实际订单金额':actual,
              '差额已交回财务金额':received,'执行阶段':'已返现',
              '运营负责人':'吴家浩','实际金额核验状态':'官方已核实'}
    fields.update(kwargs)
    return {'record_id':'rec-difference-1','fields':fields}


class DifferenceAlertTest(unittest.TestCase):
    def test_recipient_chats_use_chat_id(self):
        async def check():
            with patch.object(makeup_alert.feishu,'_req',new=AsyncMock(
                return_value={'code':0,'data':{'message_id':'om_test'}})) as request:
                item=makeup_alert.evaluate(row())
                await makeup_alert._send(item,'ops')
                await makeup_alert._send(item,'finance')
                self.assertEqual([call.kwargs['params']['receive_id_type'] for call in request.call_args_list],
                                 ['chat_id','chat_id'])
                self.assertEqual([call.kwargs['json']['receive_id'] for call in request.call_args_list],
                                 [makeup_alert.OPS_CHAT,makeup_alert.FINANCE_CHAT])
        asyncio.run(check())

    def test_only_known_executed_difference(self):
        self.assertEqual(makeup_alert.evaluate(row())['due'],50)
        self.assertEqual(makeup_alert.evaluate(row(received=50))['status'],'cleared')
        self.assertIsNone(makeup_alert.evaluate(row(actual=None)))
        self.assertIsNone(makeup_alert.evaluate(row(执行阶段='待执行')))
        self.assertIsNone(makeup_alert.evaluate(row(平台='抖音')))
        self.assertIsNone(makeup_alert.evaluate(row(order='')))
        self.assertEqual(makeup_alert.evaluate(row(实际金额核验状态='待核实'))['status'],'unverified_actual')

    def test_over_advance_goes_to_finance_review(self):
        item=makeup_alert.evaluate(row(plan=169,actual=219))
        self.assertEqual(item['due'],-50)
        self.assertIn('财务核实',item['kind'])
        self.assertIn('财务确认',makeup_alert.message(item,'finance'))

    def test_owner_change_blocks_misroute(self):
        self.assertEqual(makeup_alert.evaluate(row(运营负责人='蔡宗佑'))['status'],'owner_mismatch')

    def test_dry_run_does_not_write_or_send(self):
        with patch.object(makeup_alert.makeup,'load_records',new=AsyncMock(return_value=[row(),row(order='2',received=50),row(order='3',actual=None)])), \
             patch.object(makeup_alert,'_send',new=AsyncMock()) as send, \
             patch.object(makeup_alert,'_save_marker',new=AsyncMock()) as save:
            result=asyncio.run(makeup_alert.run(dry_run=True,official_orders=official(('3317044944002077875',169),('2',169),('3',169))))
        self.assertEqual((result['open'],result['cleared'],result['missing_amount']), (1,1,1))
        send.assert_not_called();save.assert_not_called()

    def test_send_once_per_recipient_and_retry_only_failed_side(self):
        source=row()
        saved=[]
        async def save(_id,marker):
            saved.append(json.loads(json.dumps(marker)))
        with patch.object(makeup_alert.makeup,'load_records',new=AsyncMock(return_value=[source])), \
             patch.object(makeup_alert,'_send',new=AsyncMock(side_effect=['om_ops','om_fin'])) as send, \
             patch.object(makeup_alert,'_save_marker',new=save):
            result=asyncio.run(makeup_alert.run(dry_run=False,official_orders=official(('3317044944002077875',169))))
        self.assertEqual(result['sent'],2)
        self.assertEqual(list(saved[-1]),['ops','finance'])
        source['fields'][makeup_alert.MARKER_FIELD]=json.dumps(saved[-1])
        with patch.object(makeup_alert.makeup,'load_records',new=AsyncMock(return_value=[source])), \
             patch.object(makeup_alert,'_send',new=AsyncMock()) as second_send:
            result=asyncio.run(makeup_alert.run(dry_run=False,official_orders=official(('3317044944002077875',169))))
        self.assertEqual(result['sent'],0)
        second_send.assert_not_called()

    def test_changed_due_realerts_and_cleared_resets(self):
        initial=makeup_alert.evaluate(row())
        previous={'ops':{'fingerprint':initial['fingerprint']},'finance':{'fingerprint':initial['fingerprint']}}
        changed=makeup_alert.evaluate(row(received=10,**{makeup_alert.MARKER_FIELD:json.dumps(previous)}))
        self.assertNotEqual(initial['fingerprint'],changed['fingerprint'])
        cleared=makeup_alert.evaluate(row(received=50,**{makeup_alert.MARKER_FIELD:json.dumps(previous)}))
        self.assertEqual(cleared['status'],'cleared')
        self.assertTrue(cleared['marker'])

    def test_duplicate_order_blocks_before_sending(self):
        duplicate=row()
        duplicate['record_id']='rec-duplicate'
        with patch.object(makeup_alert.makeup,'load_records',new=AsyncMock(return_value=[row(),duplicate])), \
             patch.object(makeup_alert,'_send',new=AsyncMock()) as send:
            with self.assertRaisesRegex(ValueError,'订单重复'):
                asyncio.run(makeup_alert.run(dry_run=False,official_orders=official(('3317044944002077875',169))))
        send.assert_not_called()

    def test_requires_current_month_official_scope(self):
        with patch.object(makeup_alert,'_send',new=AsyncMock()) as send:
            with self.assertRaisesRegex(ValueError,'官方结算订单'):
                asyncio.run(makeup_alert.run(dry_run=False,records=[row()]))
            result=asyncio.run(makeup_alert.run(dry_run=False,records=[row()],official_orders={}))
        self.assertEqual((result['scanned'],result['sent']),(0,0))
        send.assert_not_called()

    def test_official_amount_mismatch_blocks_chase(self):
        with patch.object(makeup_alert,'_send',new=AsyncMock()) as send:
            result=asyncio.run(makeup_alert.run(dry_run=False,records=[row()],
                                official_orders=official(('3317044944002077875',168))))
        self.assertEqual(result['official_amount_mismatch'],['3317044944002077875'])
        self.assertEqual(result['sent'],0)
        send.assert_not_called()

    def test_report_scope_uses_settlement_but_amount_uses_order_detail(self):
        csv_text='订单号,订单实际金额（元）,退款金额（元）\n3317044944002077875,150,0\nother,0,50\n'
        book=openpyxl.Workbook()
        sheet=book.active;sheet.title='export'
        sheet.append(['订单编号','买家实付金额'])
        sheet.append(['3317044944002077875',100])
        sheet.append(['3317044944002077875',69])
        buf=io.BytesIO();book.save(buf)
        raw={'source_files':[{'platform':'天猫','shop':'天猫纷岚',
             'fname':'2026-09-交易货款.csv','buf':csv_text.encode('gb18030')},
             {'platform':'天猫','shop':'天猫纷岚','fname':'9月订单详细.xlsx','buf':buf.getvalue()}]}
        amounts=settlement_engine.tmall_makeup_audit_orders(raw)
        self.assertEqual(amounts,official(('3317044944002077875',169)))
