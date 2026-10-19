import copy,io,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unittest.mock import AsyncMock,patch
import openpyxl
from app import makeup,settlement_engine as e,presentation


def record(order='a',**kw):
    f={'平台':'天猫','店铺':'纷岚旗舰店','订单号':order,'计划订单金额':219,'实际订单金额':169,'差额已交回财务金额':50,'佣金':10,'补单发货类型':'空包卡片','下单月份':'2026-09'};f.update(kw)
    return {'record_id':'rec-'+order,'fields':f}

def group():return {'s1':{'main':'a','sales':100},'s2':{'main':'a','sales':69}}

class MakeupTests(unittest.TestCase):
    def test_multi_child_one_payment(self):
        r=makeup.prepare([record()], '天猫纷岚',group(),'2026-09');self.assertEqual(1,len(r));self.assertEqual(179,r['a']['principal']+r['a']['commission'])
    def test_duplicate_blocks(self):
        with self.assertRaises(ValueError):makeup.prepare([record(),record()], '天猫纷岚',group(),'2026-09')
    def test_unrecovered_or_missing_or_extra_payment_blocks(self):
        for f in ({'差额已交回财务金额':0},{'佣金':None},{'实际订单金额':230},{'补单发货类型':None},{'计划订单金额':float('nan')}):
            with self.subTest(f=f),self.assertRaises(ValueError):makeup.prepare([record(**f)],'天猫纷岚',group(),'2026-09')
    def test_pending_not_charged(self):
        rr=[record('later')];self.assertEqual({},makeup.prepare(rr,'天猫纷岚',group(),'2026-09'));self.assertEqual(10,makeup.pending(rr,'天猫纷岚',group(),'2026-09')[0]['commission'])
    def test_refund_and_split_month_block(self):
        for sale in (0,100):
            with self.assertRaises(ValueError):makeup.prepare([record()], '天猫纷岚',{'x':{'main':'a','sales':sale}},'2026-09')
    def test_other_platform_not_matched(self):
        self.assertEqual({},makeup.prepare([record(平台='抖音')],'天猫纷岚',group(),'2026-09'))
    def test_tmall_engine_card_and_receipt(self):
        trades=[{'子订单号':'s1','订单号':'a','数量':1,'订单实际金额（元）':100,'退款金额（元）':0,'下单时间':'2026-09-01'}, {'子订单号':'s2','订单号':'a','数量':1,'订单实际金额（元）':69,'退款金额（元）':0,'下单时间':'2026-09-01'}]
        orders=[{'子订单编号':s,'商家编码':'SKU','物流单号':'W'} for s in ('s1','s2')]
        raw={'source_files':[{'platform':'天猫','shop':'天猫纷岚','fname':'交易货款.csv','buf':b'a'},{'platform':'天猫','shop':'天猫纷岚','fname':'订单详细.xlsx','buf':b'b'}], 'makeup_records':[record(),record('later')]}
        r=e.SettlementReport('2026-09')
        with patch.object(e,'read_csv',return_value=trades),patch.object(e,'sheet_rows',return_value=orders),patch.object(e,'read_tmall_fees',return_value=(0,0)),patch.object(e,'build_bill_pool',return_value={}):
            e.process_tmall(r,raw,{'SKU':{'cost':50}}, {'W':{'amount':3.8,'file':'中通','sheet':'Sheet1+Sheet2','source':'账单'}},'天猫纷岚')
        d=r.as_dict();m=d['monthly_rows'][0]
        self.assertEqual(0,m[5]);self.assertEqual(0,m[11]);self.assertEqual(179,m[13]);self.assertEqual(169,m[16]);self.assertEqual(-13.8,m[14]);self.assertEqual(2,len(r.fee_rows));self.assertEqual(1,len(r.asset_rows))
        self.assertEqual(169,sum(x[17] for x in d['sku_summary_rows'] if x[2]!='店铺合计'))
    def test_zto_returns_and_same_file_dedup(self):
        w=openpyxl.Workbook();s=w.active;s.title='Sheet1';s.append(['运单号','合计']);s.append(['W',3.1]);s=w.create_sheet('Sheet2');s.append(['运单号','向下应结总金额','结算类型']);s.append(['W',.7,'退件费']);b=io.BytesIO();w.save(b)
        f={'fname':'中通月份账单.xlsx','buf':b.getvalue()};raw={'source_files':[f,f]}
        self.assertEqual(3.1,e.build_bill_pool(raw)['W']['amount']);self.assertAlmostEqual(3.8,e.build_bill_pool(raw,True)['W']['amount'])

class LoadTests(unittest.IsolatedAsyncioTestCase):
    async def test_pagination_and_error(self):
        req=AsyncMock(side_effect=[{'code':0,'data':{'items':[record()],'has_more':True,'page_token':'next'}},{'code':0,'data':{'items':[record('b')],'has_more':False}}])
        with patch('app.feishu._req',req):self.assertEqual(2,len(await makeup.load_records()))
        self.assertEqual('next',req.call_args.kwargs['params']['page_token'])
        with patch('app.feishu._req',AsyncMock(return_value={'code':999,'msg':'denied'})),self.assertRaises(RuntimeError):await makeup.load_records()
