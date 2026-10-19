"""Tmall makeup orders: read-only register input, settlement-scoped costs."""
from decimal import Decimal, InvalidOperation
from . import feishu

VERSION = '2026-10-19.v1'
APP = 'GlKwb0POKaPl09ssNjYcrqQgn3T'
TABLE = 'tblz6o5E4m9fe7ES'


def text(v):
    if isinstance(v,list): return ''.join(text(x) for x in v)
    if isinstance(v,dict): return str(v.get('text',''))
    return '' if v is None else str(v).strip()


def amount(f, key, optional=False):
    value = text(f.get(key))
    if not value and optional: return Decimal(0)
    try:
        d = Decimal(value)
        if not d.is_finite() or d < 0: raise ValueError(key)
        return d.quantize(Decimal('.01'))
    except (InvalidOperation, ValueError):
        raise ValueError('补单金额缺失或非法：'+key)


async def load_records():
    records, seen, token = [], set(), ''
    while True:
        params = {'page_size': 500}
        if token: params['page_token'] = token
        result = await feishu._req('GET', f'/open-apis/bitable/v1/apps/{APP}/tables/{TABLE}/records', params=params)
        if result.get('code') != 0:
            raise RuntimeError(f"补单登记台读取失败，停止天猫计算：{result.get('code')} {result.get('msg')}")
        data = result.get('data', {})
        records.extend(data.get('items', []))
        if not data.get('has_more'): return records
        token = data.get('page_token')
        if not token or token in seen: raise RuntimeError('补单登记台分页异常，停止计算')
        seen.add(token)


def prepare(records, shop, grouped, period):
    """Only positive official settlement sales trigger principal/commission once.

    Refund-only and split-month payments require explicit finance resolution;
    never silently repeat a full principal/commission on a later refund.
    """
    sales, counts = {}, {}
    for row in grouped.values():
        key = row['main']
        sales[key] = sales.get(key, Decimal(0)) + Decimal(str(row['sales']))
        counts[key] = counts.get(key, 0) + 1
    matched, seen = {}, set()
    for record in records:
        f = record.get('fields', {})
        if text(f.get('平台')) != '天猫': continue
        source_shop = text(f.get('店铺'))
        canonical = '天猫纷岚' if '纷岚' in source_shop else ('天猫宝空' if '宝空' in source_shop or 'powkong' in source_shop.lower() else '')
        if canonical != shop: continue
        order = text(f.get('订单号'))
        if not order or order not in sales: continue
        if order in seen: raise ValueError(f'补单重复订单：{shop}/{order}')
        seen.add(order)
        if text(f.get('执行阶段')) in ('待执行','已取消'):
            raise ValueError(f'补单执行状态与官方结算冲突：{shop}/{order}')
        if sales[order] <= 0:
            raise ValueError(f'补单仅退款/无本期正销售，须核原结算费用：{shop}/{order}')
        actual = amount(f,'实际订单金额')
        plan = amount(f,'计划订单金额')
        recovered = amount(f,'差额已交回财务金额',True)
        commission = amount(f,'佣金')
        if plan-recovered != actual:
            raise ValueError(f'补单垫款未清或需财务确认补付：{shop}/{order}')
        if sales[order].quantize(Decimal('.01')) != actual:
            raise ValueError(f'补单官方结算与实付不一致/跨月拆单：{shop}/{order}')
        settled = text(f.get('结算月份'))
        if settled and settled.replace('/','-') != period:
            raise ValueError(f'补单已登记其他结算月：{shop}/{order}')
        kind = text(f.get('补单发货类型'))
        if kind not in ('空包卡片','实物'):
            raise ValueError(f'补单发货性质未确认：{shop}/{order}')
        matched[order] = {'principal':float(actual),'commission':float(commission),
                          'card_only':kind=='空包卡片','record_id':record['record_id']}
    return matched


def pending(records, shop, grouped, period):
    settled = {r['main'] for r in grouped.values()}
    out = []
    for r in records:
        f = r.get('fields', {})
        source_shop = text(f.get('店铺'))
        canonical = '天猫纷岚' if '纷岚' in source_shop else ('天猫宝空' if '宝空' in source_shop or 'powkong' in source_shop.lower() else '')
        order = text(f.get('订单号'))
        # Reporting-month plan cohort remains visible until settlement;
        # never charge the pending amount to the current month.
        if (text(f.get('平台')) == '天猫' and canonical == shop and order
                and order not in settled and text(f.get('下单月份')).replace('/','-') == period
                and text(f.get('执行阶段')) not in ('待执行', '已取消')):
            out.append({'order':order,'record_id':r['record_id'],'commission':float(amount(f,'佣金'))})
    return out
