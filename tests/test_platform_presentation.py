import copy
import unittest
from unittest.mock import AsyncMock,patch
from app import presentation,source_audit


def fixture():
    rows=[];products=[];traces=[];costs=[];logs=[];sources=[]
    for platform in ('抖音','天猫'):
        shop=platform+'宝空'
        rows.append(['2026-09',platform,shop,'公司',1,2,100,10,90,5,4,30,6,2,43,43/90,85,0,0,'待财务终审'])
        products.append([platform,'','中国',shop,'2026-09','SKU1','商品',2,.2,100,10,5,4,30,6,2,43,43/90])
        traces.append({'platform':platform,'shop':shop,'period':'2026-09','order':'001','sub':'002','sku':'SKU1','date':'2026-08-20 12:00:00','source':'official.xlsx:2','qty':2,'sales':100,'refund':10,'quantity_note':'商品销售数量'})
        costs.append([platform,shop,'2026-09','001','SKU1','商品',2,15,30,'ERP'])
        logs.append([platform,shop,'2026-09','001','002','SF1','顺丰',6,'账单.xlsx','明细','账单','计入'])
        sources.append([platform,shop,'交易货款','official.xlsx',1,'已读取','本月结算'])
    return dict(monthly_rows=rows,product_rows=products,trace_rows=traces,cost_rows=costs,log_rows=logs,fee_rows=[],asset_rows=[],gap_rows=[],source_rows=sources,note_rows=[],source_baselines=[{'platform':p,'shop':p+'宝空','qty':2,'sales':100,'refund':10,'platform_fee':5,'ad_fee':4,'other_fee':2,'net_receipt':85,'source':'official.xlsx','filter':'本月结算'} for p in ('抖音','天猫')])


class PresentationTests(unittest.TestCase):
    def test_platforms_are_split_and_months_reconcile(self):
        source=fixture();before=copy.deepcopy(source)
        result=presentation.build(source,'2026-09')
        self.assertEqual(before,source)
        for platform in ('抖音','天猫'):
            package=result[platform]
            self.assertEqual([],package['issues'])
            self.assertTrue(all(title.startswith(platform+'｜') for title in package['sheets']))
            self.assertEqual('2026-08',package['sheets'][platform+'｜按下单月拆分'][1][3])
            self.assertEqual(43,package['sheets'][platform+'｜月度毛利试算'][1][14])

    def test_missing_dates_and_wrong_platform_block(self):
        data=fixture();data['trace_rows'][0]['date']=''
        self.assertTrue(presentation.build(data,'2026-09')['抖音']['issues'])
        data=fixture();data['product_rows'][0][0]='天猫'
        self.assertTrue(presentation.build(data,'2026-09')['抖音']['issues'])

    def test_independent_source_difference_blocks(self):
        data=fixture();data['source_baselines'][0]['sales']=101
        issues=presentation.build(data,'2026-09')['抖音']['issues']
        self.assertTrue(any('原件' in s for s in issues),issues)

    def test_virtual_quantity_is_not_silently_accepted(self):
        data=fixture();data['trace_rows'][0]['quantity_note']='虚拟支付单位待核'
        self.assertTrue(presentation.build(data,'2026-09')['抖音']['issues'])

    def test_no_source_proof_cannot_be_marked_passed(self):
        data=fixture();data['source_baselines']=[]
        self.assertTrue(presentation.build(data,'2026-09')['天猫']['issues'])

    def test_wrong_sku_is_blocked_even_when_shop_total_matches(self):
        data=fixture();data['trace_rows'][0]['sku']='WRONG'
        self.assertTrue(any('逐SKU日期' in s for s in presentation.build(data,'2026-09')['抖音']['issues']))

    def test_missing_fee_proof_is_blocked(self):
        data=fixture();del data['source_baselines'][0]['ad_fee']
        self.assertTrue(any('广告费' in s for s in presentation.build(data,'2026-09')['抖音']['issues']))

    def test_original_actionable_gaps_are_preserved(self):
        data=fixture();gap=['P1','天猫','天猫宝空','2026-09','售后','001','回收待核','暂不抵减','财务核实']
        data['gap_rows']=[gap]
        self.assertIn(gap,presentation.build(data,'2026-09')['天猫']['sheets']['天猫｜缺口与口径'])

    def test_url_rich_text_roundtrip(self):
        url='https://example.com/?sheet=123'
        self.assertEqual(presentation.digest([[url]]),presentation.digest([[[{'type':'url','text':url,'link':url}]]]))

    def test_independent_tmall_fees_exclude_recharge_and_duplicate(self):
        fee={'fname':'平台费.csv','buf':'支出金额（元）\n5\n'.encode('utf-8')}
        ad={'fname':'0947_ads.csv','buf':'交易日期,交易类型,操作金额(元)\n2026-09-01,扣款,4\n2026-09-01,充值,100\n2026-08-01,扣款,8\n'.encode('utf-8')}
        result=source_audit.tmall([fee,fee,ad],'2026-09',[{'订单实际金额（元）':100,'退款金额（元）':10}])
        self.assertEqual((5,4,81),(result['platform_fee'],result['ad_fee'],result['net_receipt']))

    def test_douyin_missing_ad_source_stays_unknown(self):
        with patch.object(source_audit.e,'_manifest_confirmed_no_data',return_value=False):
            b=source_audit.douyin({},[],'抖音宝空',[{'结算金额':85,'平台服务费':-5}])
        self.assertEqual(85,b['net_receipt']);self.assertEqual(5,b['platform_fee'])
        self.assertNotIn('ad_fee',b)

    def test_confirmed_no_settlement_is_not_false_blocker(self):
        engine=presentation.engine;report=engine.SettlementReport('2026-09')
        with patch.object(engine,'_manifest_confirmed_no_data',return_value=True):
            engine.process_douyin(report,{}, {}, {},'抖音宝空')
        data=report.as_dict()
        self.assertEqual([],presentation.build(data,'2026-09')['抖音']['issues'])
        self.assertIn('确认',data['source_baselines'][0]['source'])


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_hidden_approval_values_cannot_diverge(self):
        platform='抖音';data=fixture()
        sheets=presentation.build(data,'2026-09')[platform]['sheets']
        ids={name:str(i) for i,name in enumerate(sheets)}
        ids[platform+'｜送审验收']='audit'
        meta={'code':0,'data':{'sheets':[{'title':n,'sheet_id':sid,'hidden':False} for n,sid in ids.items()]}}
        audit=[['格式版本',presentation.VERSION,None,None,None,None]]
        audit += [[c,'通过',None,None,None,None] for c in presentation.CHECKS]
        audit += [['PAGE',n,ids[n],len(r),max(map(len,r)),presentation.digest([x+[None]*(max(map(len,r))-len(x)) for x in r])] for n,r in sheets.items()]
        native=[data['monthly_rows'][0]]
        audit += [['NATIVE','native',500,20,presentation.digest(native),None]]
        async def read(token,sid,count,width):
            if sid=='audit':return audit
            if sid=='native':
                wrong=list(native[0]);wrong[14]=999
                return [list(presentation.engine.MONTHLY_HEADER),wrong]
            rows=sheets[next(n for n in sheets if ids[n]==sid)]
            return [r+[None]*(width-len(r)) for r in rows]
        with patch.object(presentation.feishu,'_req',AsyncMock(return_value=meta)),patch.object(presentation,'read_rows',side_effect=read):
            result=await presentation.preflight('test',platform)
        self.assertTrue(any('审批底表已改变' in x for x in result['issues']))

if __name__=='__main__':unittest.main()

