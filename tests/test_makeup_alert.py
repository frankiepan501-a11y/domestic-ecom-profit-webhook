import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import makeup_alert


def row(order='3317044944002077875', plan=219, actual=169, received=0, **kwargs):
    fields = {'平台':'天猫','店铺':'纷岚旗舰店','订单号':order,
              '计划订单金额':plan,'实际订单金额':actual,
              '差额已交回财务金额':received,'执行阶段':'已返现',
              '运营负责人':'吴家浩','实际金额核验状态':'官方已核实'}
    fields.update(kwargs)
    return {'record_id':'rec-difference-1','fields':fields}


class DifferenceAlertTest(unittest.TestCase):
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
            result=asyncio.run(makeup_alert.run(dry_run=True))
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
            result=asyncio.run(makeup_alert.run(dry_run=False))
        self.assertEqual(result['sent'],2)
        self.assertEqual(list(saved[-1]),['ops','finance'])
        source['fields'][makeup_alert.MARKER_FIELD]=json.dumps(saved[-1])
        with patch.object(makeup_alert.makeup,'load_records',new=AsyncMock(return_value=[source])), \
             patch.object(makeup_alert,'_send',new=AsyncMock()) as second_send:
            result=asyncio.run(makeup_alert.run(dry_run=False))
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
                asyncio.run(makeup_alert.run(dry_run=False))
        send.assert_not_called()
