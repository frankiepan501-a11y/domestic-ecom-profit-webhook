"""Independent source aggregations: never reads monthly/product calculated totals."""
import hashlib
from . import settlement_engine as e


def tmall(files, period, trades):
    fees=ads=0.0
    seen=set();sources=[]
    for sf in sorted(files,key=e._fname):
        name=e._fname(sf);base=e._basename(name);buf=sf.get('buf') or b''
        if not name.lower().endswith('.csv') or base.startswith('~$') or any(x in name for x in ('交易货款','平台结算账单')):
            continue
        digest=hashlib.sha256(buf).hexdigest()
        if digest in seen:continue
        seen.add(digest)
        rows=e.read_csv(buf,prefer_gbk=True)
        headers={e.norm(k) for k in rows[0]} if rows else set()
        if base.startswith('0947_') or {'交易日期','交易类型','操作金额(元)'}.issubset(headers):
            ads+=sum(e.money(e.p(r,'操作金额(元)')) for r in rows if e.norm(e.p(r,'交易日期')).startswith(period) and e.norm(e.p(r,'交易类型'))=='扣款')
            sources.append(name);continue
        if '流水' in base:continue
        for row in rows:
            amounts=[e.money(e.p(row,k)) for k in ('支出金额（元）','支出金额(元)','扣费金额(元)','扣费金额','本月付款','积分类服务费金额','账单金额')]
            fees+=next((a for a in amounts if a),0)
        sources.append(name)
    sales=sum(e.money(e.p(r,'订单实际金额（元）')) for r in trades)
    refund=sum(e.money(e.p(r,'退款金额（元）')) for r in trades)
    return {'platform_fee':fees,'ad_fee':ads,'other_fee':0,
            'net_receipt':sales-refund-fees-ads,
            'fee_proof':'；'.join(sources),
            'receipt_basis':'按官方交易货款/退款及费用原件独立复算净回款；非银行到账核销'}


def douyin(raw, files, shop, settlements):
    ads=[r for r in raw.get('ads',[]) if e.norm(r.get('platform'))=='抖音' and e.canonical_shop('抖音',e.norm(r.get('shop')))==shop]
    cash=[r for r in ads if e.norm(r.get('source_type'))=='财务流水']
    result={'platform_fee':sum(abs(e.money(e.p(r,k))) for r in settlements for k in ('平台服务费','达人佣金','站外推广费')),
            'net_receipt':sum(e.money(e.p(r,'结算金额')) for r in settlements),
            'other_fee':0,'receipt_basis':'官方结算金额逐行合计；非银行到账核销'}
    if ads or e._manifest_confirmed_no_data(raw,'抖音',shop,'广告账单'):
        result['ad_fee']=sum(abs(e.money(r.get('spend'))) for r in (cash or ads))
    for sf,rows in e._douyin_fee_files(files):
        for r in rows:
            scene=e.norm(e.p(r,'动账场景'));direction=e.norm(e.p(r,'动账方向'))
            note=e.norm(e.p(r,'备注') or e.p(r,'动账备注') or e.p(r,'摘要'))
            if scene=='小额打款' and '补差价' in note and direction=='出账':continue
            if '评价有礼' in scene and direction in ('出账','入账'):continue
            if scene in ('权益保险','退换货运费险','消费者赔付','上门取件运费','抖音月付联合贴息') and direction=='出账':
                result['other_fee']+=abs(e.money(e.p(r,'动账金额')))
            elif e.money(e.p(r,'支付保费'))>0:
                result['other_fee']+=e.money(e.p(r,'支付保费'))
    result['fee_proof']='官方结算三项费用；原始广告明细财务流水优先；动账费用/保费逐行复算'
    return result
