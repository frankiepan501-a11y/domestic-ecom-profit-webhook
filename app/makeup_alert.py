"""Remind Tmall operations and finance of unsettled makeup advance differences."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from . import feishu, makeup, config

MARKER_FIELD = '差额提醒记录（系统）'
OPS_OPEN_ID = 'ou_37ac0a6f7a0d69f9f14238cffdff78f7'  # 吴家浩，聪哥1号 namespace
FINANCE_CHAT = config.FINANCE_CONFIRM_CHAT_ID
BASE_URL = f'https://u1wpma3xuhr.feishu.cn/base/{makeup.APP}?table={makeup.TABLE}'


def _money(value):
    value = makeup.text(value)
    if not value:
        return None
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0:
            return None
        return number.quantize(Decimal('.01'))
    except (InvalidOperation, ValueError):
        return None


def _marker(value):
    try:
        parsed = json.loads(makeup.text(value) or '{}')
        if not isinstance(parsed, dict):
            raise ValueError('差额提醒记录不是对象')
        return parsed
    except ValueError:
        raise ValueError('差额提醒记录格式异常，停止发送以免重复提醒')


def evaluate(record):
    f = record.get('fields', {})
    if makeup.text(f.get('平台')) != '天猫':
        return None
    shop = makeup.text(f.get('店铺'))
    if not ('宝空' in shop or '纷岚' in shop or 'powkong' in shop.lower()):
        return None
    order = makeup.text(f.get('订单号'))
    if not order or makeup.text(f.get('执行阶段')) in ('待执行', '已取消'):
        return None
    plan = _money(f.get('计划订单金额'))
    actual = _money(f.get('实际订单金额'))
    received = _money(f.get('差额已交回财务金额'))
    if makeup.text(f.get('差额已交回财务金额')) and received is None:
        return {'record_id':record['record_id'], 'status':'invalid_received', 'order':order}
    if plan is None or actual is None or plan <= 0 or actual <= 0:
        return None
    if makeup.text(f.get('实际金额核验状态')) != '官方已核实':
        return {'record_id':record['record_id'], 'status':'unverified_actual', 'order':order}
    received = received if received is not None else Decimal('0')
    due = plan - actual - received
    if due == 0:
        return {'record_id':record['record_id'], 'status':'cleared',
                'marker':_marker(f.get(MARKER_FIELD))}
    owner = makeup.text(f.get('运营负责人'))
    if owner and owner != '吴家浩':
        return {'record_id':record['record_id'], 'status':'owner_mismatch',
                'order':order, 'owner':owner}
    kind = '追回并交回财务' if due > 0 else '财务核实补付或超额到账'
    fingerprint = hashlib.sha256(
        f'{record["record_id"]}|{order}|{plan}|{actual}|{received}|{due}|{kind}'.encode()
    ).hexdigest()[:24]
    return {'record_id':record['record_id'], 'status':'open', 'shop':shop,
            'order':order, 'plan_no':makeup.text(f.get('计划编号')),
            'plan':plan, 'actual':actual, 'received':received,
            'due':due, 'kind':kind, 'fingerprint':fingerprint,
            'marker':_marker(f.get(MARKER_FIELD))}


def message(item, target):
    head = '🟠 [FIN·P1] 天猫补单垫款差额待处理 · ' + item['shop']
    due = item['due']
    action = (f'请吴家浩追回 {due:.2f} 元，交回财务；财务到账后回填金额、日期和凭证。'
              if due > 0 else
              '实际订单金额高于计划垫款，或已到账金额超过应追回额。请财务确认是否补付或更正到账记录，运营协助核原单。')
    prefix = '阿浩，请处理这笔补单差额。' if target == 'ops' else '请财务核实这笔补单差额及到账状态。'
    plan_no = f'计划编号：{item["plan_no"]}\n' if item['plan_no'] else ''
    return (f'{head}\n{prefix}\n店铺：{item["shop"]}\n{plan_no}订单号：{item["order"]}\n'
            f'计划已垫：{item["plan"]:.2f} 元；官方实际：{item["actual"]:.2f} 元；'
            f'财务已收差额：{item["received"]:.2f} 元\n'
            f'当前差额：{due:+.2f} 元。{action}\n'
            f'回填位置：{BASE_URL}&record={item["record_id"]}')


async def _save_marker(record_id, marker):
    result = await feishu._req('PUT',
        f'/open-apis/bitable/v1/apps/{makeup.APP}/tables/{makeup.TABLE}/records/{record_id}',
        json={'fields':{MARKER_FIELD:json.dumps(marker,ensure_ascii=False,separators=(',',':'))}})
    if result.get('code') != 0:
        raise RuntimeError(f'差额提醒回执写入失败 code={result.get("code")}')


async def _send(item, target):
    recipient = OPS_OPEN_ID if target == 'ops' else FINANCE_CHAT
    id_type = 'open_id' if target == 'ops' else 'chat_id'
    key = str(uuid.uuid5(uuid.NAMESPACE_URL,
                        f'makeup-difference|{item["record_id"]}|{item["fingerprint"]}|{target}'))
    result = await feishu._req('POST','/open-apis/im/v1/messages',
        params={'receive_id_type':id_type},json={'receive_id':recipient,'msg_type':'text',
        'content':json.dumps({'text':message(item,target)},ensure_ascii=False),'uuid':key})
    if result.get('code') != 0 or not result.get('data',{}).get('message_id'):
        raise RuntimeError(f'差额提醒发送失败 target={target} code={result.get("code")}')
    return result['data']['message_id']


async def run(dry_run=True):
    records = await makeup.load_records()
    seen_orders = set()
    for record in records:
        f = record.get('fields', {})
        if makeup.text(f.get('平台')) != '天猫':
            continue
        order = makeup.text(f.get('订单号'))
        shop = makeup.text(f.get('店铺'))
        if not order or makeup.text(f.get('执行阶段')) == '已取消':
            continue
        key = (shop, order)
        if key in seen_orders:
            raise ValueError(f'补单订单重复，暂停差额提醒：{shop}/{order}')
        seen_orders.add(key)
    result = {'mode':'dry_run' if dry_run else 'send','scanned':len(records),
              'open':0,'sent':0,'already_reminded':0,'cleared':0,'missing_amount':0,
              'owner_mismatch':[],'invalid_received':[],'unverified_actual':[],
              'candidates':[]}
    for record in records:
        item = evaluate(record)
        if item is None:
            f = record.get('fields',{})
            if makeup.text(f.get('平台')) == '天猫' and makeup.text(f.get('订单号')) and not makeup.text(f.get('实际订单金额')):
                result['missing_amount'] += 1
            continue
        if item['status'] == 'owner_mismatch':
            result['owner_mismatch'].append({'order':item['order'],'owner':item['owner']})
            continue
        if item['status'] == 'invalid_received':
            result['invalid_received'].append(item['order'])
            continue
        if item['status'] == 'unverified_actual':
            result['unverified_actual'].append(item['order'])
            continue
        if item['status'] == 'cleared':
            result['cleared'] += 1
            if not dry_run and item['marker']:
                await _save_marker(item['record_id'],{})
            continue
        result['open'] += 1
        targets = [t for t in ('ops','finance') if item['marker'].get(t,{}).get('fingerprint') != item['fingerprint']]
        result['already_reminded'] += 2-len(targets)
        result['candidates'].append({'order':item['order'],'shop':item['shop'],
                                     'due':float(item['due']),'targets':targets})
        if dry_run:
            continue
        for target in targets:
            message_id = await _send(item,target)
            item['marker'][target] = {'fingerprint':item['fingerprint'],
                'message_id':message_id,'at':datetime.now(timezone.utc).isoformat()}
            await _save_marker(item['record_id'],item['marker'])
            result['sent'] += 1
    return result
