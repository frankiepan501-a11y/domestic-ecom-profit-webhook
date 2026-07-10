"""P0 card-driven monthly workflow for domestic e-commerce profit reports."""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
from datetime import datetime
from typing import Any

from fastapi import UploadFile

from . import cards, config, feishu, ledger, task_runner, task_seeder, writer


SHOP_FILE_FIELDS = [
    ("订单明细", "订单明细", True),
    ("退款明细", "退款明细", True),
    ("平台费用", "当月结算账单", True),
    ("平台费用", "平台费用", True),
    ("广告/推广", "广告账单", True),
]
LOGISTICS_FILE_FIELDS = [("物流月结账单", "物流账单", True)]
SHOP_LEGACY_FIELD_BY_TYPE = {file_type: old_field for old_field, file_type, _ in SHOP_FILE_FIELDS}
LOGISTICS_LEGACY_FIELD_BY_TYPE = {file_type: old_field for old_field, file_type, _ in LOGISTICS_FILE_FIELDS}
PLATFORM_ORDER = {"天猫": 1, "抖音": 2, "小红书": 3, "拼多多": 4, "淘宝": 5, "京东": 6, "全平台": 99}
FILE_TYPE_ORDER = {"订单明细": 1, "退款明细": 2, "当月结算账单": 3, "平台费用": 4, "广告账单": 5, "物流账单": 6}
NO_SETTLEMENT_FILE_TYPES = {"订单明细", "退款明细", "当月结算账单", "平台费用"}
DEFERABLE_SCOPE_PLATFORMS = {"淘宝", "拼多多"}
FINANCE_CONFIRM_PLATFORMS = ("抖音", "天猫", "小红书", "京东")
SETTLEMENT_FILE_KEYWORDS = (
    "当月结算账单", "结算账单", "结算订单", "交易货款", "商品结算明细",
    "订单结算明细", "订单结算明细对账", "货款明细", "settlement", "settle",
)
FILE_TYPE_KEYWORDS = {
    "订单明细": ("订单明细", "订单", "exportorderlist", "orderlist", "结算订单", "order"),
    "退款明细": ("退款明细", "退款", "售后", "refund"),
    "当月结算账单": (
        "当月结算账单", "结算账单", "结算订单", "交易货款", "商品结算明细",
        "订单结算明细", "订单结算明细对账", "货款明细", "到账", "settlement",
        "settle",
    ),
    "平台费用": (
        "平台费用", "平台费", "返点积分", "返还积分", "光合平台", "软件服务费",
        "基础软件", "类目软件服务费", "天猫佣金", "跨境服务增值费", "淘金币",
        "消费积分", "消费券", "合作费用", "服务费", "佣金", "动账", "资金明细",
        "平台支出", "权益保险", "commission",
    ),
    "广告账单": ("广告账单", "广告", "推广", "消耗", "账户流水", "ad", "ads", "marketing"),
    "物流账单": ("物流账单", "物流", "快递", "月结", "顺丰", "中通", "极兔", "京东物流", "运费", "waybill"),
}
PLATFORM_ALIASES = {
    "天猫": ("天猫", "tmall"),
    "抖音": ("抖音", "douyin"),
    "小红书": ("小红书", "xhs", "red"),
    "拼多多": ("拼多多", "pdd"),
    "淘宝": ("淘宝", "taobao"),
    "京东": ("京东", "jd"),
}
BRAND_SHOP_ALIASES = {
    "powkong": ("powkong", "宝空", "宝宝", "宝控"),
    "funlab": ("funlab", "纷岚", "梵乐璞"),
    "cube": ("正方体", "正方体电玩"),
}
UPLOAD_FILE_CONCURRENCY = 3

TERMINAL_RUN_STATES = {
    "本期无结算已确认",
    "财务确认",
    "已归档",
    "已写汇总",
    "财务接受临时估算",
}


def _prev_month() -> str:
    now = datetime.now()
    year = now.year if now.month > 1 else now.year - 1
    month = now.month - 1 or 12
    return f"{year}-{month:02d}"


def _ftext(value: Any) -> str:
    return ledger.extract_text(value)


def _attachments(value: Any) -> list[dict]:
    return value if isinstance(value, list) else []


def _manifest_attachments(fields: dict) -> list[dict]:
    atts = _attachments(fields.get("附件"))
    if atts:
        return _merge_attachments([], atts)
    raw = _ftext(fields.get("file_token_json"))
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return _merge_attachments([], [x for x in parsed if isinstance(x, dict)])
        except Exception:
            return []
    return []


def _merge_attachments(existing: list[dict], new_items: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    order: list[str] = []
    for item in existing + new_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("file_name") or "")
        basename = _file_basename(name)
        token = str(item.get("file_token") or "")
        key = f"name:{_norm(basename)}" if basename else f"token:{token}"
        if not key or key == "token:":
            continue
        if key not in merged:
            order.append(key)
        next_item = dict(item)
        if basename:
            next_item["name"] = basename
        merged[key] = next_item
    return [merged[k] for k in order]


def _norm(value: str) -> str:
    drop = " \t\r\n/_-—·.（）()[]【】{}:：,，;；"
    text = value.lower()
    for ch in drop:
        text = text.replace(ch, "")
    return text


def _file_basename(path: str) -> str:
    return path.replace("\\", "/").split("/")[-1] or "upload.bin"


def _file_type_matches(file_type: str, path: str) -> bool:
    normalized = _norm(path)
    if file_type == "订单明细" and any(_norm(k) in normalized for k in SETTLEMENT_FILE_KEYWORDS):
        return False
    return any(_norm(k) in normalized for k in FILE_TYPE_KEYWORDS.get(file_type, (file_type,)))


def _platform_matches(platform: str, normalized_path: str) -> bool:
    aliases = PLATFORM_ALIASES.get(platform, (platform,))
    return any(_norm(a) in normalized_path for a in aliases if a)


def _shop_aliases(platform: str, shop: str) -> tuple[str, ...]:
    aliases: list[str] = [shop]
    text = f"{platform} {shop}".lower()
    if "powkong" in text or "宝空" in text:
        aliases.extend(BRAND_SHOP_ALIASES["powkong"])
    if "funlab" in text or "纷岚" in text or "梵乐璞" in text:
        aliases.extend(BRAND_SHOP_ALIASES["funlab"])
    if "正方体" in text:
        aliases.extend(BRAND_SHOP_ALIASES["cube"])
    seen = set()
    out = []
    for alias in aliases:
        key = _norm(alias)
        if key and key not in seen:
            seen.add(key)
            out.append(alias)
    return tuple(out)


def _shop_matches(platform: str, shop: str, normalized_path: str) -> bool:
    return any(_norm(a) in normalized_path for a in _shop_aliases(platform, shop))


def _manifest_sort_key(rec: dict) -> tuple:
    f = rec.get("fields", {})
    platform = _ftext(f.get("平台"))
    shop = _ftext(f.get("店铺"))
    file_type = _ftext(f.get("文件类型"))
    return (PLATFORM_ORDER.get(platform, 50), shop, FILE_TYPE_ORDER.get(file_type, 50), file_type)


def _month_matches(record: dict, year_month: str) -> bool:
    return _ftext(record.get("fields", {}).get("月份")) == year_month


def _month_filter(year_month: str) -> dict:
    return {
        "conjunction": "and",
        "conditions": [
            {"field_name": "月份", "operator": "is", "value": [year_month]},
        ],
    }


async def _legacy_rows(year_month: str) -> list[dict]:
    rows = await feishu.bitable_search_records(
        config.TASK_APP_TOKEN,
        config.TASK_TABLE_ID,
        filter_obj=_month_filter(year_month),
        page_size=50,
        field_names=[
            "任务标题", "数据类型", "月份", "平台", "店铺", "快递公司",
            "订单明细", "退款明细", "平台费用", "广告/推广", "物流月结账单",
            "责任人", "报表飞书链接", "任务状态",
        ],
    )
    return [r for r in rows if _month_matches(r, year_month)]


async def _legacy_summary_record_id(year_month: str, rows: list[dict] | None = None) -> str:
    for rec in rows if rows is not None else await _legacy_rows(year_month):
        f = rec.get("fields", {})
        if f.get("数据类型") == "月度报表汇总":
            return rec["record_id"]
    return ""


def _manifest_id(run_id: str, platform: str, shop: str, file_type: str) -> str:
    fid = hashlib.sha1(f"{run_id}:{platform}:{shop}:{file_type}".encode("utf-8")).hexdigest()[:14]
    return f"fm_{fid}"


def _manifest_key(platform: str, shop: str, file_type: str) -> tuple[str, str, str]:
    return platform, shop, file_type


def _manifest_lookup(existing: list[dict]) -> dict[tuple[str, str, str], dict]:
    out: dict[tuple[str, str, str], dict] = {}
    for rec in existing:
        f = rec.get("fields", {})
        out[_manifest_key(_ftext(f.get("平台")), _ftext(f.get("店铺")), _ftext(f.get("文件类型")))] = rec
    return out


async def _get_or_create_manifest(existing: dict[tuple[str, str, str], dict], run_id: str,
                                  platform: str, shop: str, month: str,
                                  file_type: str, required: bool) -> dict:
    key = _manifest_key(platform, shop, file_type)
    rec = existing.get(key)
    if rec:
        return rec
    manifest_id = _manifest_id(run_id, platform, shop, file_type)
    await ledger.create(ledger.FILE_TABLE, {
        "file_manifest_id": manifest_id,
        "run_id": run_id,
        "平台": platform,
        "店铺": shop,
        "月份": month,
        "文件类型": file_type,
        "必交": required,
        "状态": "待提交",
        "最后动作时间": ledger.now_ms(),
    })
    rec = {
        "record_id": "",
        "fields": {
            "file_manifest_id": manifest_id,
            "run_id": run_id,
            "平台": platform,
            "店铺": shop,
            "月份": month,
            "文件类型": file_type,
            "必交": required,
            "状态": "待提交",
        },
    }
    existing[key] = rec
    return rec


async def _ensure_manifests(run_id: str, year_month: str, rows: list[dict] | None = None) -> list[str]:
    checklist: list[str] = []
    rows = rows if rows is not None else await _legacy_rows(year_month)
    existing = _manifest_lookup(await ledger.manifests_for_run(run_id))
    for row in rows:
        f = row.get("fields", {})
        dtype = f.get("数据类型")
        platform = _ftext(f.get("平台"))
        shop = _ftext(f.get("店铺"))
        title = _ftext(f.get("任务标题"))
        if dtype == "店铺数据":
            for old_field, file_type, required in SHOP_FILE_FIELDS:
                rec = await _get_or_create_manifest(existing, run_id, platform, shop, year_month, file_type, required)
                mid = _ftext(rec.get("fields", {}).get("file_manifest_id"))
                checklist.append(f"{platform}/{shop} - {file_type}")
                atts = _attachments(f.get(old_field))
                if atts:
                    await ledger.mark_manifest(mid, {
                        "状态": "已提交",
                        "file_token_json": ledger.compact_json(atts),
                        "parser结果": f"legacy_record_id={row['record_id']}; legacy_field={old_field}",
                    })
        elif dtype == "物流账单":
            for old_field, file_type, required in LOGISTICS_FILE_FIELDS:
                rec = await _get_or_create_manifest(existing, run_id, "全平台", "全公司", year_month, file_type, required)
                mid = _ftext(rec.get("fields", {}).get("file_manifest_id"))
                checklist.append(f"{title or '物流月结账单'} - {file_type}")
                atts = _attachments(f.get(old_field))
                if atts:
                    await ledger.mark_manifest(mid, {
                        "状态": "已提交",
                        "file_token_json": ledger.compact_json(atts),
                        "parser结果": f"legacy_record_id={row['record_id']}; legacy_field={old_field}",
                    })
    return checklist


async def _resolve_union_targets(open_ids: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for oid, name in open_ids.items():
        union_id = await feishu.open_id_to_union_id(oid)
        if union_id:
            out[union_id] = name or oid
    return out


async def _ops_union_targets(frankie_only: bool = False) -> dict[str, str]:
    if frankie_only:
        return {config.FRANKIE_UNION_ID: "潘志聪"}
    users = await feishu.resolve_users_jt_fallback(config.REMIND_OPS_DEPT_ROOTS, config.REMIND_OPS_JOB_TITLES)
    targets = await _resolve_union_targets(users)
    if not targets:
        targets[config.FRANKIE_UNION_ID] = "潘志聪"
    return targets


async def _finance_union_targets(frankie_only: bool = False) -> dict[str, str]:
    if frankie_only:
        return {config.FRANKIE_UNION_ID: "潘志聪"}
    users = await feishu.resolve_users_jt_fallback(config.REMIND_FINANCE_DEPT_ROOTS, config.REMIND_FINANCE_JOB_TITLES)
    users.setdefault(config.FRANKIE_OPEN_ID, "潘志聪")
    targets = await _resolve_union_targets(users)
    if not targets:
        targets[config.FRANKIE_UNION_ID] = "潘志聪"
    return targets


async def _send_card_to_union_targets(card: dict, targets: dict[str, str]) -> list[str]:
    sent: list[str] = []
    for union_id in targets:
        res = await feishu.send_interactive_union_id(union_id, card, use_event_app=True)
        mid = (res.get("data") or {}).get("message_id")
        if mid:
            sent.append(mid)
    return sent


async def _send_finance_confirm_card(card: dict, *, frankie_only: bool = False) -> tuple[list[str], list[str], str]:
    if frankie_only:
        targets = await _finance_union_targets(frankie_only=True)
        sent = await _send_card_to_union_targets(card, targets)
        return sent, list(targets.values()), "private"
    if not config.FINANCE_CONFIRM_CHAT_ID:
        raise RuntimeError("FINANCE_CONFIRM_CHAT_ID is required for production finance confirmation cards")
    res = await feishu.send_interactive_chat(config.FINANCE_CONFIRM_CHAT_ID, card, use_event_app=True)
    mid = (res.get("data") or {}).get("message_id")
    if not mid:
        raise RuntimeError(
            "finance confirmation group card send failed: "
            f"code={res.get('code')} msg={res.get('msg')}"
        )
    return [mid], [config.FINANCE_CONFIRM_CHAT_NAME], "group"


async def send_monthly_intake(year_month: str | None = None, *, force: bool = False,
                              dry_run: bool = False, frankie_only: bool = False) -> dict:
    year_month = year_month or _prev_month()
    rows = await _legacy_rows(year_month)
    if not rows:
        await task_seeder.ensure_month_rows(year_month)
        rows = await _legacy_rows(year_month)
    legacy_summary = await _legacy_summary_record_id(year_month, rows)
    run = await ledger.ensure_run(year_month, legacy_summary)
    run_id = _ftext(run.get("fields", {}).get("run_id")) or ledger.run_id_for_month(year_month)
    checklist = await _ensure_manifests(run_id, year_month, rows)
    card = cards.operation_submit_card(run_id, year_month, checklist)
    if dry_run:
        return {"dry_run": True, "run_id": run_id, "card": card, "checklist_count": len(checklist)}
    targets = await _ops_union_targets(frankie_only=frankie_only)
    sent = await _send_card_to_union_targets(card, targets)
    for mid in sent:
        await ledger.append_message_id(run_id, mid)
    await ledger.update_run(run_id, "待运营提交", "send_operation_submit_card",
                            "等待运营提交资料/确认无广告/无结算")
    await ledger.write_audit(
        f"send_intake:{run_id}:{','.join(sent)}",
        "send_operation_submit_card",
        "system",
        run_id,
        "run",
        run_id,
        {},
        {"message_ids": sent, "target_count": len(targets)},
        {"force": force, "frankie_only": frankie_only},
        "sent",
    )
    return {"run_id": run_id, "year_month": year_month, "sent": sent, "targets": list(targets.values())}


async def send_sample_cards(year_month: str | None = None, *, send: bool = False) -> dict:
    """Build or send the three P0 card contracts to Frankie for smoke verification."""
    year_month = year_month or _prev_month()
    run_id = f"domestic-ecom-profit-smoke-{year_month}"
    ops_card = cards.operation_submit_card(run_id, year_month, [
        "京东/京东纷岚店 - 订单明细",
        "京东/京东纷岚店 - 广告账单",
        "物流月结账单(全公司) - 物流账单",
    ])
    gap_card = cards.p0_gap_card({
        "fields": {
            "gap_id": f"gap_smoke_{year_month.replace('-', '')}",
            "run_id": run_id,
            "平台": "京东",
            "月份": year_month,
            "P级": "P0",
            "缺口类型": "ERP_SKU映射缺失",
            "SKU/order/waybill": "商家编码为空或无法映射 ERP SKU",
            "证据": "smoke: 订单明细第 12 行缺商家编码",
            "影响金额": 0,
            "可回放id": "smoke-replay-001",
        }
    })
    finance_card = cards.finance_confirm_card({
        "fields": {
            "output_id": f"out_smoke_{year_month.replace('-', '')}",
            "run_id": run_id,
            "平台": "天猫",
            "workbook_token": "smoke-workbook-token",
            "workbook链接": "smoke-workbook-token",
            "产品毛利月度": True,
            "财务决定": "",
        }
    }, {"fields": {"run_id": run_id, "期间": year_month}}, [])
    return_followup_card = cards.finance_return_followup_card(
        run_id=run_id,
        period=year_month,
        platform="天猫",
        output_id=f"out_smoke_{year_month.replace('-', '')}",
        return_kind="combined",
    )
    sample_cards = {
        "ops_submit": ops_card,
        "p0_gap": gap_card,
        "finance_confirm": finance_card,
        "finance_return_followup": return_followup_card,
    }
    if not send:
        return {"dry_run": True, "run_id": run_id, "cards": sample_cards}
    sent: dict[str, list[str]] = {}
    for name, card in sample_cards.items():
        sent[name] = await _send_card_to_union_targets(card, {config.FRANKIE_UNION_ID: "潘志聪"})
    return {"run_id": run_id, "sent": sent}


async def _create_missing_gaps(run_id: str, period: str) -> list[dict]:
    manifests = await ledger.manifests_for_run(run_id)
    existing = await ledger.gaps_for_run(run_id)
    existing_keys = {
        (_ftext(g.get("fields", {}).get("缺口类型")),
         _ftext(g.get("fields", {}).get("平台")),
         _ftext(g.get("fields", {}).get("证据")))
        for g in existing
    }
    created: list[dict] = []
    for rec in manifests:
        f = rec.get("fields", {})
        status = _ftext(f.get("状态"))
        file_type = _ftext(f.get("文件类型"))
        platform = _ftext(f.get("平台"))
        shop = _ftext(f.get("店铺"))
        required = bool(f.get("必交"))
        if status in ("已提交", "已解析", "已确认无数据", "已关闭"):
            continue
        if not required:
            continue
        if file_type == "广告账单":
            gap_type = "广告证据缺失"
            evidence = f"{platform}/{shop} 未提交广告账单，也未确认本月无广告消耗"
        elif file_type == "物流账单":
            gap_type = "物流账单缺失"
            evidence = "物流账单需覆盖前月、本月、次月可归属尾单"
        else:
            gap_type = "其他"
            evidence = f"{platform}/{shop} 缺少 {file_type}"
        key = (gap_type, platform, evidence)
        if key not in existing_keys:
            created.append(await ledger.create_gap(run_id, gap_type, platform, period, evidence))
            existing_keys.add(key)
    return created


async def send_open_gap_cards(run_id: str, *, frankie_only: bool = False) -> list[str]:
    gaps = await ledger.open_p0_gaps(run_id)
    if not gaps:
        return []
    targets = await _ops_union_targets(frankie_only=frankie_only)
    sent: list[str] = []
    for gap in gaps:
        gf = gap.get("fields", {})
        if _ftext(gf.get("message_id")):
            continue
        card = cards.p0_gap_card(gap)
        mids = await _send_card_to_union_targets(card, targets)
        if mids:
            gid = _ftext(gf.get("gap_id"))
            await ledger.mark_gap(gid, {"message_id": ",".join(mids)})
            sent.extend(mids)
    return sent


async def _finance_return_targets(return_kind: str) -> dict[str, str]:
    if config.CARD_WORKFLOW_FRANKIE_ONLY:
        return {config.FRANKIE_UNION_ID: "潘志聪"}
    if return_kind == "data":
        return await _ops_union_targets(frankie_only=False)
    if return_kind == "combined":
        targets = await _ops_union_targets(frankie_only=False)
        targets.setdefault(config.FRANKIE_UNION_ID, "潘志聪")
        return targets
    return {config.FRANKIE_UNION_ID: "潘志聪"}


async def _send_finance_return_followup(run_id: str, period: str, platform: str,
                                        output_id: str, return_kind: str,
                                        output: dict | None = None) -> dict:
    output = output or (await ledger.find_first(ledger.OUTPUT_TABLE, "output_id", output_id) if output_id else None)
    workbook_url = _link_url((output or {}).get("fields", {}).get("workbook链接"))
    if not period:
        run = await ledger.find_first(ledger.RUN_TABLE, "run_id", run_id)
        period = _ftext((run or {}).get("fields", {}).get("期间"))
    card = cards.finance_return_followup_card(
        run_id=run_id,
        period=period,
        platform=platform,
        output_id=output_id,
        return_kind=return_kind,
        workbook_url=workbook_url,
    )
    targets = await _finance_return_targets(return_kind)
    sent = await _send_card_to_union_targets(card, targets)
    for mid in sent:
        await ledger.append_message_id(run_id, mid)
    audit_suffix = ledger.payload_hash({
        "run_id": run_id,
        "output_id": output_id,
        "platform": platform,
        "return_kind": return_kind,
        "message_ids": sent,
        "ts": ledger.now_ms(),
    })[:12]
    await ledger.write_audit(
        f"send_finance_return_followup:{run_id}:{output_id}:{platform}:{return_kind}:{audit_suffix}",
        "send_finance_return_followup_card",
        "system",
        run_id,
        "output",
        output_id,
        {},
        {"message_ids": sent, "return_kind": return_kind, "platform": platform},
        {"frankie_only": config.CARD_WORKFLOW_FRANKIE_ONLY, "targets": list(targets.values())},
        "sent" if sent else "not_sent",
    )
    return {"sent": sent, "targets": list(targets.values()), "return_kind": return_kind}


async def initial_gate_and_maybe_run(run_id: str, period: str) -> dict:
    await _create_missing_gaps(run_id, period)
    await _close_resolved_missing_gaps(run_id)
    open_gaps = await ledger.open_p0_gaps(run_id)
    if open_gaps:
        await ledger.update_run(run_id, "P0待补件", "initial_gate",
                                f"仍有 {len(open_gaps)} 个 P0 缺口待处理")
        sent = await send_open_gap_cards(run_id, frankie_only=config.CARD_WORKFLOW_FRANKIE_ONLY)
        return {"ready": False, "open_p0": len(open_gaps), "gap_cards": sent}
    await start_trial_run(run_id)
    return {"ready": True, "trial_started": True}


async def start_trial_run(run_id: str) -> None:
    run = await ledger.find_first(ledger.RUN_TABLE, "run_id", run_id)
    if not run:
        return
    status = _ftext(run.get("fields", {}).get("当前状态"))
    if status in TERMINAL_RUN_STATES or status == "试算中":
        return
    asyncio.create_task(_trial_task(run_id))


async def _trial_task(run_id: str) -> None:
    run = await ledger.find_first(ledger.RUN_TABLE, "run_id", run_id)
    if not run:
        return
    rf = run.get("fields", {})
    period = _ftext(rf.get("期间"))
    legacy_id = _ftext(rf.get("legacy_summary_record_id"))
    if not legacy_id:
        legacy_id = await _legacy_summary_record_id(period)
    if not legacy_id:
        await ledger.update_run(run_id, "试算失败待排查", "trial_no_legacy_summary",
                                "旧任务台未找到月度汇总行，无法复用现有计算入口")
        return
    await ledger.update_run(run_id, "试算中", "trial_started", "")
    result = await task_runner.run_profit(legacy_id)
    if not result.get("ok"):
        await ledger.update_run(run_id, "试算失败待排查", "trial_failed",
                                str(result.get("error", ""))[:900])
        return
    workbook_url = result.get("url", "")
    token = workbook_url.rstrip("/").split("/")[-1]
    has_monthly = False
    has_quarterly = False
    try:
        meta = await feishu.sheets_metainfo(token)
        names = {s.get("title") for s in (meta.get("data") or {}).get("sheets", [])}
        has_monthly = "月度毛利试算" in names
        has_quarterly = "产品毛利_季度" in names
    except Exception:
        names = set()
    output = await ledger.create_output(
        run_id,
        workbook_url,
        "月度平台毛利报表确认：统计口径=结算月；涉税金额不在本卡核对，季度初另走涉税核对卡。",
        has_monthly=has_monthly,
        has_quarterly=has_quarterly,
    )
    if not has_monthly:
        await ledger.update_run(run_id, "试算失败待排查", "finance_gate_blocked",
                                f"workbook 缺少月度毛利 gate sheet: {sorted(names)}")
        return
    await send_finance_card(run_id, output, frankie_only=config.CARD_WORKFLOW_FRANKIE_ONLY)


async def send_finance_card(run_id: str, output: dict | None = None,
                            *, frankie_only: bool = False,
                            platform: str = "",
                            grant_access: bool = True) -> dict:
    run = await ledger.find_first(ledger.RUN_TABLE, "run_id", run_id)
    output = output or await ledger.latest_output(run_id)
    if not run or not output:
        return {"sent": [], "error": "missing run/output"}
    if not platform:
        return await send_platform_finance_cards(run_id, output, frankie_only=frankie_only)
    if grant_access:
        await _grant_output_workbook_access(output)
    gaps = await ledger.gaps_for_run(run_id)
    platform_output = _with_output_platform(output, platform)
    report_summary = await _platform_report_summary(platform_output, platform)
    card = cards.finance_confirm_card(platform_output, run, gaps, report_summary=report_summary)
    sent, targets, target_mode = await _send_finance_confirm_card(card, frankie_only=frankie_only)
    output_id = _ftext(platform_output.get("fields", {}).get("output_id"))
    if sent:
        await ledger.update(ledger.OUTPUT_TABLE, platform_output["record_id"], {"确认卡message_id": ",".join(sent)})
    await ledger.update_run(run_id, "待财务确认", "send_platform_finance_confirm_card",
                            f"等待财务确认 {platform} 当月毛利报表")
    return {
        "sent": sent,
        "targets": targets,
        "target_mode": target_mode,
        "output_id": output_id,
        "platform": platform,
    }


async def send_platform_finance_cards(run_id: str, output: dict,
                                      *, frankie_only: bool = False,
                                      platforms: tuple[str, ...] = FINANCE_CONFIRM_PLATFORMS) -> dict:
    await _grant_output_workbook_access(output)
    sent_by_platform: dict[str, dict] = {}
    for platform in platforms:
        platform_output = await _ensure_platform_output(output, platform)
        sent_by_platform[platform] = await send_finance_card(
            run_id,
            platform_output,
            frankie_only=frankie_only,
            platform=platform,
            grant_access=False,
        )
    return {"run_id": run_id, "platforms": list(platforms), "sent_by_platform": sent_by_platform}


def _with_output_platform(output: dict, platform: str) -> dict:
    cloned = dict(output)
    fields = dict(output.get("fields", {}))
    fields["平台"] = platform
    cloned["fields"] = fields
    return cloned


async def _ensure_platform_output(output: dict, platform: str) -> dict:
    fields = output.get("fields", {})
    run_id = _ftext(fields.get("run_id"))
    workbook_url = _link_url(fields.get("workbook链接"))
    platform_output = await ledger.create_output(
        run_id,
        workbook_url,
        f"月度平台毛利报表确认：平台={platform}；统计口径=结算月；涉税金额不在本卡核对，季度初另走涉税核对卡。",
        has_monthly=bool(fields.get("产品毛利月度")),
        has_quarterly=bool(fields.get("产品毛利季度")),
        platform=platform,
    )
    return _with_output_platform(platform_output, platform)


def _sheet_token_from_url(url: str) -> str:
    if "/sheets/" not in url:
        return ""
    return url.rstrip("/").split("/")[-1].split("?")[0].split("#")[0]


async def _grant_output_workbook_access(output: dict) -> dict:
    """Finance cards must never point at a workbook the owner cannot open."""
    fields = output.get("fields", {})
    workbook_url = _link_url(fields.get("workbook链接"))
    token = _sheet_token_from_url(workbook_url)
    if not token:
        return {"granted": 0, "failed": 0, "members": 0, "skipped": "not_sheet_url"}
    return await writer._grant_report_collaborators(token)


def _link_url(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("link") or value.get("url") or value.get("text") or "")
    if isinstance(value, list):
        for item in value:
            url = _link_url(item)
            if url:
                return url
        return ""
    return str(value or "")


def _to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    text = text.replace(",", "").replace("¥", "").replace("￥", "").replace("%", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _ratio_value(value: Any) -> float:
    if isinstance(value, str) and "%" in value:
        return _to_float(value) / 100
    return _to_float(value)


async def _sheet_id_by_title(spreadsheet_token: str, title: str) -> str:
    meta = await feishu.sheets_metainfo(spreadsheet_token)
    for sheet in (meta.get("data") or {}).get("sheets", []):
        if sheet.get("title") == title:
            return str(sheet.get("sheetId") or "")
    return ""


async def _platform_report_summary(output: dict, platform: str) -> dict:
    fields = output.get("fields", {})
    workbook_url = _link_url(fields.get("workbook链接"))
    token = _sheet_token_from_url(workbook_url)
    if not token:
        return {"stores": [], "issues": [], "blocking_issues": ["报表链接缺失，无法读取店铺毛利数据。"]}
    try:
        sheet_id = await _sheet_id_by_title(token, "月度毛利试算")
        if not sheet_id:
            return {"stores": [], "issues": [], "blocking_issues": ["报表缺少“月度毛利试算”sheet。"]}
        res = await feishu.sheets_values_get(token, f"{sheet_id}!A1:T500")
    except Exception as e:
        return {"stores": [], "issues": [], "blocking_issues": [f"读取月度毛利试算失败：{e}"]}
    if res.get("code") not in (0, None):
        return {
            "stores": [],
            "issues": [],
            "blocking_issues": [f"读取月度毛利试算失败：{res.get('code')} {res.get('msg')}"],
        }
    value_range = (res.get("data") or {}).get("valueRange") or {}
    values = value_range.get("values") or []
    if len(values) < 2:
        return {"stores": [], "issues": [], "blocking_issues": ["月度毛利试算没有店铺数据。"]}
    header = [str(x or "").strip() for x in values[0]]
    stores: list[dict] = []
    issues: list[str] = []
    blocking_issues: list[str] = []
    for raw in values[1:]:
        row = {header[i]: raw[i] if i < len(raw) else "" for i in range(len(header))}
        if _ftext(row.get("平台")) != platform:
            continue
        shop = _ftext(row.get("店铺")) or "-"
        net_sales = _to_float(row.get("净销售额(RMB)"))
        gross_profit = _to_float(row.get("试算毛利(RMB)"))
        margin = _ratio_value(row.get("试算毛利率"))
        cost = _to_float(row.get("采购成本(RMB)"))
        logistics = _to_float(row.get("尾程费用(RMB)"))
        orders = _to_float(row.get("销售订单数"))
        p0_count = _to_float(row.get("P0缺口数"))
        status = _ftext(row.get("状态"))
        stores.append({
            "shop": shop,
            "orders": orders,
            "qty": _to_float(row.get("销量")),
            "net_sales": net_sales,
            "ad_fee": _to_float(row.get("广告费(RMB)")),
            "procurement_cost": cost,
            "logistics_cost": logistics,
            "gross_profit": gross_profit,
            "gross_margin": row.get("试算毛利率"),
            "status": status or "已生成",
        })
        if p0_count > 0:
            blocking_issues.append(f"{shop} 仍有资料或成本缺失 {int(p0_count)} 项。")
        if net_sales > 0 and cost <= 0:
            blocking_issues.append(f"{shop} 有销售额但采购成本为 0，请补成本或确认成本来源。")
        if orders > 0 and logistics <= 0:
            blocking_issues.append(f"{shop} 有订单但物流费用为 0，请确认物流账单或无物流原因。")
        if gross_profit < 0:
            issues.append(f"{shop} 毛利为负（毛利额 {gross_profit:,.2f}，毛利率 {margin * 100:.2f}%），请确认业务原因；这不是自动判定的资料缺口。")
        if status and any(key in status for key in ("缺口", "失败", "异常", "待补")):
            blocking_issues.append(f"{shop} 报表状态为“{status}”，请先处理后再定稿。")
    if not stores:
        blocking_issues.append(f"月度毛利试算没有读取到 {platform} 店铺行。")
    return {
        "stores": stores,
        "issues": issues,
        "blocking_issues": blocking_issues,
        "sheet": "月度毛利试算",
    }


async def _finance_platform_done_summary(run_id: str, workbook_url: str) -> tuple[int, int]:
    done = 0
    total = len(FINANCE_CONFIRM_PLATFORMS)
    for platform in FINANCE_CONFIRM_PLATFORMS:
        output_id = ledger.output_id_for(run_id, workbook_url, platform)
        rec = await ledger.find_first(ledger.OUTPUT_TABLE, "output_id", output_id)
        decision = _ftext((rec or {}).get("fields", {}).get("财务决定"))
        if decision in (f"{platform}：确认定稿", f"{platform}：接受临时估算"):
            done += 1
    return done, total


async def _create_output_from_latest_legacy_report(year_month: str,
                                                   workbook_url: str = "") -> tuple[str, dict | None]:
    rows = await _legacy_rows(year_month)
    legacy_summary = await _legacy_summary_record_id(year_month, rows)
    run = await ledger.ensure_run(year_month, legacy_summary)
    run_id = _ftext(run.get("fields", {}).get("run_id")) or ledger.run_id_for_month(year_month)
    if not workbook_url and legacy_summary:
        current = await feishu.bitable_get_record(config.TASK_APP_TOKEN, config.TASK_TABLE_ID, legacy_summary)
        current_fields = ((current.get("data") or {}).get("record") or {}).get("fields") or {}
        workbook_url = _link_url(current_fields.get("报表飞书链接"))
    if not workbook_url:
        return run_id, None
    token = workbook_url.rstrip("/").split("/")[-1]
    names: set[str] = set()
    try:
        meta = await feishu.sheets_metainfo(token)
        names = {s.get("title") for s in (meta.get("data") or {}).get("sheets", [])}
    except Exception:
        names = set()
    output = await ledger.create_output(
        run_id,
        workbook_url,
        "A/B PASS：月度毛利、产品毛利、SKU成本、物流匹配、费用明细、缺口清单均已通过；涉税金额核对另走季度卡片；淘宝/拼多多本期暂缓，后续补做。",
        has_monthly="月度毛利试算" in names,
        has_quarterly="产品毛利_季度" in names,
    )
    return run_id, output


async def send_finance_confirm_for_month(year_month: str | None = None, *,
                                         workbook_url: str = "",
                                         dry_run: bool = False,
                                         frankie_only: bool = True) -> dict:
    year_month = year_month or _prev_month()
    run_id, output = await _create_output_from_latest_legacy_report(year_month, workbook_url)
    if not output:
        return {"sent": [], "error": "missing workbook_url", "run_id": run_id, "year_month": year_month}
    if dry_run:
        run = await ledger.find_first(ledger.RUN_TABLE, "run_id", run_id)
        gaps = await ledger.gaps_for_run(run_id)
        cards_by_platform = {}
        for platform in FINANCE_CONFIRM_PLATFORMS:
            platform_output = await _ensure_platform_output(output, platform)
            report_summary = await _platform_report_summary(platform_output, platform)
            cards_by_platform[platform] = cards.finance_confirm_card(
                platform_output,
                run or {},
                gaps,
                report_summary=report_summary,
            )
        return {
            "dry_run": True,
            "run_id": run_id,
            "platforms": list(FINANCE_CONFIRM_PLATFORMS),
            "target_mode": "private" if frankie_only else "group",
            "targets": ["潘志聪"] if frankie_only else [config.FINANCE_CONFIRM_CHAT_NAME],
            "cards": cards_by_platform,
        }
    return await send_finance_card(run_id, output, frankie_only=frankie_only)


def _deep_get(d: Any, path: list[str]) -> Any:
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _parse_value(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def extract_callback_context(body: dict) -> dict:
    value = (
        _deep_get(body, ["event", "action", "value"]) or
        _deep_get(body, ["body", "event", "action", "value"]) or
        _deep_get(body, ["action", "value"]) or
        body.get("card_action") or
        body.get("value") or
        {}
    )
    value = _parse_value(value)
    operator_open_id = (
        _deep_get(body, ["event", "operator", "open_id"]) or
        _deep_get(body, ["body", "event", "operator", "open_id"]) or
        _deep_get(body, ["operator", "open_id"]) or
        body.get("operator_open_id") or
        ""
    )
    message_id = (
        _deep_get(body, ["event", "context", "open_message_id"]) or
        _deep_get(body, ["body", "event", "context", "open_message_id"]) or
        _deep_get(body, ["event", "open_message_id"]) or
        _deep_get(body, ["data", "card_open_message_id"]) or
        body.get("open_message_id") or
        body.get("message_id") or
        ""
    )
    chat_id = (
        _deep_get(body, ["event", "context", "open_chat_id"]) or
        _deep_get(body, ["body", "event", "context", "open_chat_id"]) or
        body.get("open_chat_id") or
        body.get("chat_id") or
        ""
    )
    form_value = (
        _deep_get(body, ["event", "action", "form_value"]) or
        _deep_get(body, ["body", "event", "action", "form_value"]) or
        body.get("card_form_value") or
        {}
    )
    return {
        "value": value,
        "operator_open_id": operator_open_id,
        "message_id": message_id,
        "chat_id": chat_id,
        "form_value": form_value,
    }


async def _patch_or_reply(message_id: str, chat_id: str, operator_open_id: str, card: dict) -> dict:
    if message_id:
        res = await feishu.patch_message_card(message_id, card, use_event_app=True)
        if res.get("code") == 0:
            return {"patched_original_card": True, "response": res}
    if chat_id:
        res = await feishu.send_interactive_chat(chat_id, card, use_event_app=True)
        return {"fallback_chat_card": True, "response": res}
    if operator_open_id:
        res = await feishu.send_interactive_open_id(operator_open_id, card, use_event_app=True)
        return {"fallback_operator_card": True, "response": res}
    return {"patched_original_card": False, "fallback": False}


async def handle_callback(body: dict) -> dict:
    ctx = extract_callback_context(body)
    value = ctx["value"]
    action = str(value.get("action") or "")
    if not action.startswith("domestic_profit_"):
        return {"ignored": True, "reason": "not_domestic_profit_action", "action": action}
    run_id = str(value.get("run_id") or "")
    period = str(value.get("period") or "")
    idempotency_key = str(value.get("idempotency_key") or ledger.payload_hash(value))
    duplicate = await ledger.audit_exists(idempotency_key)
    if duplicate:
        card = cards.processed_card("✅ 国内电商报表卡片已处理", "系统已处理过这次点击，重复点击没有副作用。", details={
            "报表批次": run_id,
            "处理编号": idempotency_key[:10],
        })
        patch = await _patch_or_reply(ctx["message_id"], ctx["chat_id"], ctx["operator_open_id"], card)
        return {"duplicate": True, "patch": patch}
    await ledger.write_audit(
        idempotency_key,
        action,
        ctx["operator_open_id"],
        run_id,
        "callback",
        run_id,
        {},
        {"message": "callback started"},
        {"value": value, "form_value": ctx["form_value"]},
        "started",
        ctx["message_id"],
    )

    before: dict[str, Any] = {}
    result_message = "已处理。"
    ok = True
    target_type = "run"
    target_id = run_id

    if action == "domestic_profit_ops_files_uploaded":
        await ledger.update_run(run_id, "资料初检中", action, "系统正在检查资料清单和 P0 gate")
        gate = await initial_gate_and_maybe_run(run_id, period)
        result_message = f"已进入资料初检。P0缺口数={gate.get('open_p0', 0)}。"
    elif action == "domestic_profit_ops_no_ad":
        for rec in await ledger.manifests_for_run(run_id):
            f = rec.get("fields", {})
            if _ftext(f.get("文件类型")) == "广告账单":
                await ledger.mark_manifest(_ftext(f.get("file_manifest_id")), {
                    "状态": "已确认无数据",
                    "无数据确认": True,
                    "上传人": ctx["operator_open_id"],
                    "来源message_id": ctx["message_id"],
                })
        for gap in await ledger.gaps_for_run(run_id):
            gf = gap.get("fields", {})
            if _ftext(gf.get("缺口类型")) == "广告证据缺失":
                await ledger.mark_gap(_ftext(gf.get("gap_id")), {
                    "处理结果": "确认无数据",
                    "是否可定稿": True,
                })
        gate = await initial_gate_and_maybe_run(run_id, period)
        result_message = f"已记录本月无广告消耗确认。P0缺口数={gate.get('open_p0', 0)}。"
    elif action == "domestic_profit_ops_no_settlement":
        await ledger.update_run(run_id, "本期无结算已确认", action, "运营确认本期无结算，旁路归档")
        result_message = "已记录本期无结算，run 进入旁路终态。"
    elif action == "domestic_profit_ops_note":
        await ledger.update_run(run_id, "待运营提交", action, "运营补充说明待处理")
        result_message = "已记录补充说明入口。P0 版本请在上传页或卡片线程补充说明/附件。"
    elif action.startswith("domestic_profit_gap_"):
        gap_id = str(value.get("gap_id") or "")
        target_type = "gap"
        target_id = gap_id
        if action == "domestic_profit_gap_file_added":
            await ledger.mark_gap(gap_id, {"处理结果": "已补文件", "是否可定稿": True})
            result_message = "已记录缺口已补充文件。"
        elif action == "domestic_profit_gap_no_data":
            await ledger.mark_gap(gap_id, {"处理结果": "确认无数据", "是否可定稿": True})
            result_message = "已记录确认无数据。"
        elif action == "domestic_profit_gap_accept_temp":
            await ledger.mark_gap(gap_id, {
                "处理结果": "接受临时估算",
                "是否可定稿": True,
                "临时估算方法": "运营卡片确认接受历史临时估算；需财务确认后定稿",
            })
            result_message = "已记录接受历史临时估算，作为例外进入财务确认。"
        elif action == "domestic_profit_gap_to_finance":
            await ledger.mark_gap(gap_id, {"处理结果": "转财务判断", "是否可定稿": True})
            result_message = "已转财务判断。"
        gate = await initial_gate_and_maybe_run(run_id, period)
        result_message += f" 当前P0缺口数={gate.get('open_p0', 0)}。"
    elif action.startswith("domestic_profit_return_"):
        output_id = str(value.get("output_id") or "")
        platform = str(value.get("platform") or "")
        platform_prefix = f"{platform}：" if platform else ""
        target_type = "output"
        target_id = output_id
        out = await ledger.find_first(ledger.OUTPUT_TABLE, "output_id", output_id) if output_id else None
        if action == "domestic_profit_return_data_resolved":
            await ledger.update_run(run_id, "资料初检中", action, f"{platform_prefix}退回资料已补，重新检查资料")
            gate = await initial_gate_and_maybe_run(run_id, period)
            result_message = (
                f"{platform_prefix}已进入资料重新检查。"
                f"当前需补资料/成本缺口数={gate.get('open_p0', 0)}；"
                "无缺口时系统会自动重新试算并再发该平台确认卡。"
            )
        elif action == "domestic_profit_return_method_resolved":
            await ledger.update_run(run_id, "待AI试算", action, f"{platform_prefix}金额/口径已修正，重新试算")
            await start_trial_run(run_id)
            result_message = f"{platform_prefix}已重新进入试算；生成新报表后会自动重新发送财务确认卡。"
        elif action == "domestic_profit_return_combined_resolved":
            await ledger.update_run(run_id, "资料初检中", action, f"{platform_prefix}资料和金额口径已处理，重新检查资料")
            gate = await initial_gate_and_maybe_run(run_id, period)
            result_message = (
                f"{platform_prefix}资料和金额口径处理已记录，已重新检查资料。"
                f"当前需补资料/成本缺口数={gate.get('open_p0', 0)}；"
                "无缺口时系统会自动重新试算并再发确认卡。"
            )
        else:
            ok = False
            result_message = f"未知退回处理 action: {action}"
        if out and ok:
            await ledger.update(ledger.OUTPUT_TABLE, out["record_id"], {
                "财务决定": f"{platform_prefix}退回问题已处理，待重新确认",
            })
    elif action.startswith("domestic_profit_finance_"):
        output_id = str(value.get("output_id") or "")
        platform = str(value.get("platform") or "")
        platform_prefix = f"{platform}：" if platform else ""
        target_type = "output"
        target_id = output_id
        out = await ledger.find_first(ledger.OUTPUT_TABLE, "output_id", output_id) if output_id else None
        if action == "domestic_profit_finance_approve":
            if out:
                await ledger.update(ledger.OUTPUT_TABLE, out["record_id"], {
                    "财务决定": f"{platform_prefix}确认定稿",
                    "财务确认人": ctx["operator_open_id"],
                    "确认时间": ledger.now_ms(),
                })
            if platform and out:
                workbook_url = _link_url(out.get("fields", {}).get("workbook链接"))
                done, total = await _finance_platform_done_summary(run_id, workbook_url)
                if done >= total:
                    await ledger.update_run(run_id, "已归档", action, "四个平台毛利确认卡均已完成")
                    result_message = f"{platform} 毛利报表已确认定稿；{done}/{total} 平台已完成，报表运行台已归档。"
                else:
                    await ledger.update_run(run_id, "待财务确认", action, f"{platform} 已确认，等待其他平台确认")
                    result_message = f"{platform} 毛利报表已确认定稿；当前 {done}/{total} 平台已完成。"
            else:
                await ledger.update_run(run_id, "已归档", action, "财务已确认定稿")
                result_message = "财务已确认定稿，报表运行台已归档。"
        elif action == "domestic_profit_finance_return_data_gap":
            if out:
                await ledger.update(ledger.OUTPUT_TABLE, out["record_id"], {"财务决定": f"{platform_prefix}退回资料缺口"})
            await ledger.update_run(run_id, "财务退回资料缺口", action, f"财务退回：{platform_prefix}资料缺口")
            follow = await _send_finance_return_followup(run_id, period, platform, output_id, "data", out)
            result_message = (
                f"已退回{platform_prefix}资料缺口，状态回到运营补件链路。"
                f"已发送后续处理卡 {len(follow.get('sent') or [])} 张。"
            )
        elif action == "domestic_profit_finance_return_method_gap":
            if out:
                await ledger.update(ledger.OUTPUT_TABLE, out["record_id"], {"财务决定": f"{platform_prefix}退回口径问题"})
            await ledger.update_run(run_id, "财务退回口径问题", action, f"财务退回：{platform_prefix}口径问题")
            follow = await _send_finance_return_followup(run_id, period, platform, output_id, "method", out)
            result_message = (
                f"已退回{platform_prefix}金额/口径问题，等待修正或解释后重新试算。"
                f"已发送后续处理卡 {len(follow.get('sent') or [])} 张。"
            )
        elif action == "domestic_profit_finance_return_data_and_method_gap":
            if out:
                await ledger.update(ledger.OUTPUT_TABLE, out["record_id"], {"财务决定": f"{platform_prefix}退回资料缺口和口径问题"})
            await ledger.update_run(run_id, "财务退回资料和口径问题", action, f"财务退回：{platform_prefix}资料缺口和口径问题")
            follow = await _send_finance_return_followup(run_id, period, platform, output_id, "combined", out)
            result_message = (
                f"已退回{platform_prefix}资料缺口和金额/口径问题，后续需要补资料并修正/解释后再确认。"
                f"已发送后续处理卡 {len(follow.get('sent') or [])} 张。"
            )
        elif action == "domestic_profit_finance_accept_temp":
            if out:
                await ledger.update(ledger.OUTPUT_TABLE, out["record_id"], {
                    "财务决定": f"{platform_prefix}接受临时估算",
                    "财务确认人": ctx["operator_open_id"],
                    "确认时间": ledger.now_ms(),
                })
            if platform and out:
                workbook_url = _link_url(out.get("fields", {}).get("workbook链接"))
                done, total = await _finance_platform_done_summary(run_id, workbook_url)
                if done >= total:
                    await ledger.update_run(run_id, "财务接受临时估算", action, "四个平台毛利确认卡均已完成，含临时估算")
                    result_message = f"{platform} 已确认定稿并接受上述例外；{done}/{total} 平台已完成，进入旁路终态。"
                else:
                    await ledger.update_run(run_id, "待财务确认", action, f"{platform} 已接受临时估算，等待其他平台确认")
                    result_message = f"{platform} 已确认定稿并接受上述例外；当前 {done}/{total} 平台已完成。"
            else:
                await ledger.update_run(run_id, "财务接受临时估算", action, "财务接受临时估算旁路终态")
                result_message = "财务已确认定稿并接受临时估算/暂缓说明，进入旁路终态。"
    else:
        ok = False
        result_message = f"未知 action: {action}"

    await ledger.finalize_audit(
        idempotency_key,
        action,
        ctx["operator_open_id"],
        run_id,
        target_type,
        target_id,
        before,
        {"message": result_message, "ok": ok},
        {"value": value, "form_value": ctx["form_value"]},
        "ok" if ok else "unknown_action",
        ctx["message_id"],
    )
    processed = cards.processed_card(
        "✅ 国内电商报表卡片已处理" if ok else "⚠️ 国内电商报表卡片未处理",
        result_message,
        ok=ok,
        details={"报表批次": run_id, "处理对象": target_id, "处理编号": idempotency_key[:10]},
    )
    patch = await _patch_or_reply(ctx["message_id"], ctx["chat_id"], ctx["operator_open_id"], processed)
    return {"ok": ok, "action": action, "run_id": run_id, "patch": patch}


async def upload_page(run_id: str, token: str) -> str:
    if token != ledger.upload_token(run_id, "run"):
        return "<h3>Invalid upload token</h3>"
    manifests = await ledger.manifests_for_run(run_id)
    manifests = sorted(manifests, key=_manifest_sort_key)
    rows = [_manifest_row_html(rec, run_id, token) for rec in manifests]
    total = len(manifests)
    done = sum(1 for rec in manifests if _is_manifest_done(rec.get("fields", {})))
    pending = total - done
    safe_run = html.escape(run_id)
    safe_token = html.escape(token)
    return f"""
<!doctype html>
<html><head><meta charset="utf-8"><title>国内电商资料上传</title>
<style>
  :root {{ --ink:#172033; --muted:#667085; --line:#d8dee8; --panel:#f7f9fc; --blue:#1d5cff; --green:#137333; --red:#b42318; --amber:#9a6700; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:"Microsoft YaHei", "Segoe UI", sans-serif; color:var(--ink); margin:0; background:#fff; }}
  main {{ max-width:1180px; margin:28px auto 44px; padding:0 22px; }}
  h1 {{ font-size:24px; margin:0 0 8px; letter-spacing:0; }}
  p {{ margin:6px 0; }}
  .topbar {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:18px; }}
  .meta {{ color:var(--muted); font-size:13px; }}
  .summary {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }}
  .pill {{ border:1px solid var(--line); border-radius:999px; padding:4px 10px; font-size:12px; background:#fff; }}
  .panel {{ border:1px solid var(--line); border-radius:8px; background:var(--panel); padding:14px; margin:14px 0; }}
  .panel h2 {{ font-size:16px; margin:0 0 8px; }}
  .batch-row {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
  table {{ width:100%; border-collapse:collapse; table-layout:fixed; border:1px solid var(--line); }}
  th, td {{ border-bottom:1px solid var(--line); padding:10px 8px; vertical-align:top; font-size:13px; }}
  th {{ background:#eef3fb; text-align:left; font-weight:700; }}
  tr.done {{ background:#f6fff8; }}
  tr.pending {{ background:#fff; }}
  tr.blocked {{ background:#fff9ef; }}
  .status {{ display:inline-block; border-radius:999px; padding:3px 8px; font-size:12px; border:1px solid var(--line); background:#fff; white-space:nowrap; }}
  .status.done {{ color:var(--green); border-color:#b9e2c1; background:#ecfdf3; }}
  .status.pending {{ color:#344054; }}
  .status.blocked {{ color:var(--amber); border-color:#f0d28a; background:#fff8df; }}
  .files {{ color:var(--muted); line-height:1.45; overflow-wrap:anywhere; }}
  .actions {{ display:grid; gap:7px; }}
  .inline-form {{ display:flex; gap:6px; align-items:center; flex-wrap:wrap; }}
  input[type=file] {{ max-width:230px; }}
  input[type=text] {{ min-width:190px; padding:6px 8px; border:1px solid var(--line); border-radius:6px; }}
  button {{ border:1px solid var(--line); background:#fff; color:var(--ink); border-radius:6px; padding:6px 10px; cursor:pointer; font-size:13px; }}
  button.primary {{ background:var(--blue); color:#fff; border-color:var(--blue); }}
  button.warn {{ color:var(--amber); border-color:#f0d28a; background:#fffaf0; }}
  button:disabled {{ cursor:not-allowed; opacity:.48; }}
  .hint {{ color:var(--muted); font-size:12px; }}
  .danger {{ color:var(--red); }}
  #batchResult {{ margin-top:10px; font-size:13px; white-space:pre-wrap; }}
  @media (max-width: 860px) {{
    .topbar {{ display:block; }}
    table, thead, tbody, th, td, tr {{ display:block; }}
    thead {{ display:none; }}
    tr {{ border:1px solid var(--line); margin:10px 0; border-radius:8px; overflow:hidden; }}
    td {{ border-bottom:0; }}
    td::before {{ content:attr(data-label); display:block; font-weight:700; margin-bottom:4px; color:#344054; }}
  }}
</style></head>
<body>
<main>
  <div class="topbar">
    <div>
      <h1>国内电商毛利报表资料工作台</h1>
      <p class="meta"><b>run_id:</b> {safe_run}</p>
      <p class="meta">Base 只做 ledger；这里逐项上传或确认无数据，运营无需进入任务台改状态。</p>
      <div class="summary">
        <span class="pill">资料项 {total}</span>
        <span class="pill">已闭环 {done}</span>
        <span class="pill">待处理 {pending}</span>
      </div>
    </div>
    <form method="post" action="/upload/submit">
      <input type="hidden" name="run_id" value="{safe_run}">
      <input type="hidden" name="token" value="{safe_token}">
      <button class="primary" type="submit">提交初检</button>
    </form>
  </div>

  <section class="panel">
    <h2>文件夹批量上传</h2>
    <p class="hint">建议文件夹或文件名包含“平台 / 店铺 / 文件类型”。能精确匹配的文件会自动写入对应资料项；无法匹配的文件会列出来，不会静默入账。文件夹上传会先传到系统，再写飞书附件和 ledger；文件多时请等待页面返回结果。</p>
    <form id="batchForm" class="batch-row">
      <input type="hidden" name="run_id" value="{safe_run}">
      <input type="hidden" name="token" value="{safe_token}">
      <input id="folderInput" type="file" webkitdirectory directory multiple required>
      <button class="primary" type="submit">上传文件夹并自动归类</button>
      <span class="hint">示例：2026-06/天猫/POWKONG旗舰店/订单明细.xlsx</span>
    </form>
    <div id="batchResult"></div>
  </section>

  <table>
    <thead>
      <tr>
        <th style="width:12%;">平台</th>
        <th style="width:16%;">店铺</th>
        <th style="width:12%;">资料项</th>
        <th style="width:12%;">状态</th>
        <th style="width:22%;">已上传/证据</th>
        <th style="width:26%;">操作</th>
      </tr>
    </thead>
    <tbody>{''.join(rows) if rows else '<tr><td colspan="6">未找到资料清单，请先发起 run。</td></tr>'}</tbody>
  </table>
</main>
<script>
const form = document.getElementById('batchForm');
const out = document.getElementById('batchResult');
form.addEventListener('submit', async (event) => {{
  event.preventDefault();
  const input = document.getElementById('folderInput');
  if (!input.files.length) return;
  const fd = new FormData();
  fd.append('run_id', form.querySelector('input[name=run_id]').value);
  fd.append('token', form.querySelector('input[name=token]').value);
  for (const file of input.files) {{
    fd.append('files', file, file.webkitRelativePath || file.name);
  }}
  const button = form.querySelector('button[type=submit]');
  button.disabled = true;
  out.textContent = `正在上传 ${{input.files.length}} 个文件，并写入飞书附件/ledger。文件多时可能需要 1-3 分钟，请勿刷新或关闭页面。`;
  try {{
    const resp = await fetch('/upload/batch', {{ method: 'POST', body: fd }});
    const text = await resp.text();
    document.open();
    document.write(text);
    document.close();
  }} catch (err) {{
    button.disabled = false;
    out.textContent = `上传请求失败：${{err && err.message ? err.message : err}}。请保留文件夹，稍后重试；已成功写入的旧附件不会被清空。`;
  }}
}});
</script>
</body></html>
"""


async def handle_upload(run_id: str, token: str, file_manifest_id: str,
                        files: list[UploadFile]) -> dict:
    if token != ledger.upload_token(run_id, "run"):
        return {"ok": False, "error": "invalid token"}
    manifest = await ledger.find_first(ledger.FILE_TABLE, "file_manifest_id", file_manifest_id)
    if not manifest:
        return {"ok": False, "error": "file_manifest_id not found"}
    uploaded = await _upload_files(files)
    if not uploaded:
        return {"ok": False, "error": "no file uploaded"}
    merged, legacy_mirror = await _store_manifest_upload(manifest, uploaded)
    await ledger.update_run(run_id, "资料初检中", "upload_page", "运营已通过上传页补充资料")
    await _write_page_audit(
        "upload_file",
        run_id,
        file_manifest_id,
        before=manifest.get("fields", {}),
        after={"uploaded": uploaded, "merged_count": len(merged), "legacy_mirror": legacy_mirror},
        idempotency_suffix=ledger.payload_hash({"uploaded": uploaded, "ts": ledger.now_ms()})[:16],
    )
    return {"ok": True, "file_manifest_id": file_manifest_id, "uploaded": uploaded,
            "attachments": merged, "legacy_mirror": legacy_mirror}


async def handle_batch_upload(run_id: str, token: str, files: list[UploadFile]) -> dict:
    if token != ledger.upload_token(run_id, "run"):
        return {"ok": False, "error": "invalid token"}
    manifests = await ledger.manifests_for_run(run_id)
    by_id = {_ftext(r.get("fields", {}).get("file_manifest_id")): r for r in manifests}
    grouped: dict[str, list[UploadFile]] = {}
    unmatched: list[str] = []
    ambiguous: list[str] = []
    for f in files:
        match = _match_manifest_for_file(f.filename or "", manifests)
        if match.get("status") == "matched":
            grouped.setdefault(match["file_manifest_id"], []).append(f)
        elif match.get("status") == "ambiguous":
            ambiguous.append(f.filename or "upload.bin")
        else:
            unmatched.append(f.filename or "upload.bin")

    uploaded_groups: list[dict] = []
    for manifest_id, file_group in grouped.items():
        manifest = by_id.get(manifest_id)
        if not manifest:
            continue
        uploaded = await _upload_files(file_group)
        if not uploaded:
            continue
        merged, legacy_mirror = await _store_manifest_upload(manifest, uploaded)
        mf = manifest.get("fields", {})
        uploaded_groups.append({
            "file_manifest_id": manifest_id,
            "label": f"{_ftext(mf.get('平台'))}/{_ftext(mf.get('店铺'))}/{_ftext(mf.get('文件类型'))}",
            "uploaded": [x.get("name") for x in uploaded],
            "attachment_count": len(merged),
            "legacy_mirror": legacy_mirror,
        })

    if uploaded_groups:
        await ledger.update_run(run_id, "资料初检中", "upload_folder", "运营已通过上传页批量上传资料")
        await _write_page_audit(
            "upload_folder",
            run_id,
            run_id,
            before={},
            after={"uploaded_groups": uploaded_groups, "unmatched": unmatched, "ambiguous": ambiguous},
            idempotency_suffix=ledger.payload_hash({"groups": uploaded_groups, "ts": ledger.now_ms()})[:16],
        )
    return {"ok": True, "uploaded_groups": uploaded_groups, "unmatched": unmatched, "ambiguous": ambiguous}


async def handle_manifest_action(run_id: str, token: str, file_manifest_id: str,
                                 action_name: str, note: str = "") -> dict:
    if token != ledger.upload_token(run_id, "run"):
        return {"ok": False, "error": "invalid token"}
    manifest = await ledger.find_first(ledger.FILE_TABLE, "file_manifest_id", file_manifest_id)
    if not manifest:
        return {"ok": False, "error": "file_manifest_id not found"}
    f = manifest.get("fields", {})
    file_type = _ftext(f.get("文件类型"))
    platform = _ftext(f.get("平台"))
    shop = _ftext(f.get("店铺"))
    attachments = _manifest_attachments(f)
    note = note.strip()
    before = dict(f)

    if action_name in ("confirm_no_ad", "confirm_no_settlement") and attachments:
        return {"ok": False, "error": "该资料项已有附件，不能直接改为无数据确认；如需修正请先补充说明。"}

    if action_name == "confirm_no_ad":
        if file_type != "广告账单":
            return {"ok": False, "error": "只有广告账单资料项可以确认无广告消耗"}
        await ledger.mark_manifest(file_manifest_id, {
            "状态": "已确认无数据",
            "无数据确认": True,
            "上传人": "upload_page",
            "来源message_id": "upload_page",
            "parser结果": _page_parser_result("confirm_no_ad", note),
        })
        await _close_related_gaps(run_id, platform, shop, "广告证据缺失", "确认无数据")
        await ledger.update_run(run_id, "资料初检中", "upload_page_no_ad", f"{platform}/{shop} 已确认无广告消耗")
        message = f"已记录 {platform}/{shop} 本月无广告消耗。"
    elif action_name == "confirm_no_settlement":
        if file_type not in NO_SETTLEMENT_FILE_TYPES:
            return {"ok": False, "error": "只有订单/退款/平台费用资料项可以确认该店本月无结算"}
        touched = await _mark_shop_no_settlement(run_id, platform, shop, note)
        await ledger.update_run(run_id, "资料初检中", "upload_page_no_settlement", f"{platform}/{shop} 已确认本月无结算")
        message = f"已记录 {platform}/{shop} 本月无结算，已同步关闭 {touched} 个该店铺结算资料项。"
    elif action_name == "defer_platform_scope":
        if platform not in DEFERABLE_SCOPE_PLATFORMS:
            return {"ok": False, "error": "当前只允许淘宝/拼多多走本期暂缓口径；其他平台请补资料或走财务判断。"}
        reason = note or f"{platform} 毛利报表口径未统一，{_ftext(f.get('月份'))} 暂缓纳入本期四平台试算，后续口径统一后补充。"
        touched = await _defer_platform_scope(run_id, platform, reason)
        gap = await ledger.create_gap(
            run_id,
            "平台口径暂缓",
            platform,
            _ftext(f.get("月份")),
            reason,
            p_level="P1",
        )
        await ledger.mark_gap(_ftext(gap.get("fields", {}).get("gap_id")), {
            "处理结果": "本期暂缓，后续补充",
            "是否可定稿": True,
        })
        await ledger.update_run(run_id, "资料初检中", "upload_page_defer_platform",
                                f"{platform} 已标记为本期暂缓，不纳入本次试算")
        message = f"已记录 {platform} 本期暂缓，已同步关闭 {touched} 个该平台资料项；后续口径统一后可单独补做。"
    elif action_name == "logistics_missing":
        if file_type != "物流账单":
            return {"ok": False, "error": "只有物流账单资料项可以提交暂缺说明"}
        await ledger.mark_manifest(file_manifest_id, {
            "状态": "待补充",
            "parser结果": _page_parser_result("logistics_missing", note),
        })
        await ledger.create_gap(run_id, "物流账单缺失", platform, _ftext(f.get("月份")),
                                note or "运营提交物流账单暂缺说明")
        await ledger.update_run(run_id, "P0待补件", "upload_page_logistics_missing", "物流账单暂缺，等待补件或财务判断")
        message = "已记录物流账单暂缺说明，P0 gate 仍会阻断试算直到补账单或走财务例外判断。"
    elif action_name == "note":
        if not note:
            return {"ok": False, "error": "补充说明不能为空"}
        await ledger.mark_manifest(file_manifest_id, {
            "parser结果": _page_parser_result("note", note),
        })
        await ledger.update_run(run_id, "资料初检中", "upload_page_note", f"{platform}/{shop}/{file_type} 已补充说明")
        message = "已记录补充说明。"
    else:
        return {"ok": False, "error": f"unknown action: {action_name}"}

    await _write_page_audit(
        action_name,
        run_id,
        file_manifest_id,
        before=before,
        after={"message": message, "note": note},
        idempotency_suffix=ledger.payload_hash({"note": note, "action": action_name})[:16],
    )
    return {"ok": True, "message": message}


async def submit_upload_gate(run_id: str, token: str) -> dict:
    if token != ledger.upload_token(run_id, "run"):
        return {"ok": False, "error": "invalid token"}
    run = await ledger.find_first(ledger.RUN_TABLE, "run_id", run_id)
    period = _ftext(run.get("fields", {}).get("期间")) if run else run_id.rsplit("-", 2)[-2] + "-" + run_id.rsplit("-", 1)[-1]
    gate = await initial_gate_and_maybe_run(run_id, period)
    await _write_page_audit(
        "submit_initial_gate",
        run_id,
        run_id,
        before={},
        after=gate,
        idempotency_suffix=ledger.payload_hash({"gate": gate, "ts": ledger.now_ms()})[:16],
    )
    return {"ok": True, "gate": gate}


async def _upload_files(files: list[UploadFile]) -> list[dict]:
    sem = asyncio.Semaphore(UPLOAD_FILE_CONCURRENCY)

    async def _one(f: UploadFile) -> dict | None:
        async with sem:
            content = await f.read()
            original_name = f.filename or "upload.bin"
            base_name = _file_basename(original_name)
            res = await feishu.drive_upload_bitable_file(base_name, content, config.LEDGER_APP_TOKEN)
            file_token = (res.get("data") or {}).get("file_token")
            if file_token:
                return {"file_token": file_token, "name": base_name}
            return None

    uploaded = []
    for item in await asyncio.gather(*[_one(f) for f in files]):
        if item:
            uploaded.append(item)
    return uploaded


async def _store_manifest_upload(manifest: dict, uploaded: list[dict]) -> tuple[list[dict], dict]:
    f = manifest.get("fields", {})
    file_manifest_id = _ftext(f.get("file_manifest_id"))
    existing = _manifest_attachments(f)
    merged = _merge_attachments(existing, uploaded)
    legacy_mirror = await _mirror_uploaded_files_to_legacy(manifest, merged)
    await ledger.mark_manifest(file_manifest_id, {
        "状态": "已提交",
        "附件": merged,
        "file_token_json": ledger.compact_json(merged),
        "无数据确认": False,
        "parser结果": (
            "upload_page; "
            f"legacy_record_id={legacy_mirror.get('record_id', '')}; "
            f"legacy_field={legacy_mirror.get('field', '')}; "
            f"legacy_mirrored={str(bool(legacy_mirror.get('ok'))).lower()}"
        ),
    })
    return merged, legacy_mirror


def _match_manifest_for_file(path: str, manifests: list[dict]) -> dict:
    candidates: list[tuple[int, str]] = []
    normalized = _norm(path)
    for rec in manifests:
        f = rec.get("fields", {})
        manifest_id = _ftext(f.get("file_manifest_id"))
        platform = _ftext(f.get("平台"))
        shop = _ftext(f.get("店铺"))
        file_type = _ftext(f.get("文件类型"))
        if not manifest_id or not _file_type_matches(file_type, path):
            continue
        if file_type == "物流账单":
            candidates.append((10, manifest_id))
            continue
        if not _shop_matches(platform, shop, normalized):
            continue
        score = 20
        if platform and _platform_matches(platform, normalized):
            score += 10
        score += min(len(_norm(shop)), 6)
        candidates.append((score, manifest_id))
    if not candidates:
        return {"status": "unmatched"}
    candidates.sort(reverse=True)
    best_score = candidates[0][0]
    best = [mid for score, mid in candidates if score == best_score]
    if len(best) > 1:
        return {"status": "ambiguous", "candidates": best}
    return {"status": "matched", "file_manifest_id": best[0]}


async def _mark_shop_no_settlement(run_id: str, platform: str, shop: str, note: str) -> int:
    touched = 0
    for rec in await ledger.manifests_for_run(run_id):
        f = rec.get("fields", {})
        if _ftext(f.get("平台")) != platform or _ftext(f.get("店铺")) != shop:
            continue
        file_type = _ftext(f.get("文件类型"))
        if file_type not in NO_SETTLEMENT_FILE_TYPES:
            continue
        if _manifest_attachments(f):
            continue
        manifest_id = _ftext(f.get("file_manifest_id"))
        await ledger.mark_manifest(manifest_id, {
            "状态": "已确认无数据",
            "无数据确认": True,
            "上传人": "upload_page",
            "来源message_id": "upload_page",
            "parser结果": _page_parser_result("confirm_no_settlement", note),
        })
        await _close_related_gaps(run_id, platform, shop, "其他", "确认无数据")
        touched += 1
    return touched


async def _defer_platform_scope(run_id: str, platform: str, reason: str) -> int:
    touched = 0
    for rec in await ledger.manifests_for_run(run_id):
        f = rec.get("fields", {})
        if _ftext(f.get("平台")) != platform:
            continue
        manifest_id = _ftext(f.get("file_manifest_id"))
        await ledger.mark_manifest(manifest_id, {
            "状态": "已关闭",
            "无数据确认": False,
            "上传人": "upload_page",
            "来源message_id": "upload_page",
            "parser结果": _page_parser_result("defer_platform_scope", reason),
        })
        touched += 1
    await _close_platform_gaps(run_id, platform, "本期暂缓，后续补充")
    return touched


async def _close_related_gaps(run_id: str, platform: str, shop: str, gap_type: str, result: str) -> None:
    for gap in await ledger.gaps_for_run(run_id):
        gf = gap.get("fields", {})
        if _ftext(gf.get("缺口类型")) != gap_type:
            continue
        if platform and _ftext(gf.get("平台")) != platform:
            continue
        evidence = _ftext(gf.get("证据"))
        if shop and shop not in evidence:
            continue
        await ledger.mark_gap(_ftext(gf.get("gap_id")), {
            "处理结果": result,
            "是否可定稿": True,
        })


async def _close_platform_gaps(run_id: str, platform: str, result: str) -> None:
    for gap in await ledger.gaps_for_run(run_id):
        gf = gap.get("fields", {})
        if _ftext(gf.get("平台")) != platform:
            continue
        await ledger.mark_gap(_ftext(gf.get("gap_id")), {
            "处理结果": result,
            "是否可定稿": True,
        })


async def _close_resolved_missing_gaps(run_id: str) -> None:
    resolved: set[tuple[str, str, str]] = set()
    for rec in await ledger.manifests_for_run(run_id):
        f = rec.get("fields", {})
        if not _is_manifest_done(f):
            continue
        platform = _ftext(f.get("平台"))
        shop = _ftext(f.get("店铺"))
        file_type = _ftext(f.get("文件类型"))
        if file_type == "广告账单":
            resolved.add(("广告证据缺失", platform, f"{platform}/{shop} 未提交广告账单，也未确认本月无广告消耗"))
        elif file_type != "物流账单":
            resolved.add(("其他", platform, f"{platform}/{shop} 缺少 {file_type}"))

    if not resolved:
        return
    for gap in await ledger.gaps_for_run(run_id):
        gf = gap.get("fields", {})
        key = (
            _ftext(gf.get("缺口类型")),
            _ftext(gf.get("平台")),
            _ftext(gf.get("证据")),
        )
        if key not in resolved:
            continue
        await ledger.mark_gap(_ftext(gf.get("gap_id")), {
            "处理结果": "已补文件",
            "是否可定稿": True,
        })


def _page_parser_result(action: str, note: str = "") -> str:
    base = f"upload_page_action={action}"
    return f"{base}; note={note[:800]}" if note else base


async def _write_page_audit(action: str, run_id: str, target_id: str,
                            before: Any, after: Any, idempotency_suffix: str) -> None:
    await ledger.write_audit(
        f"upload_page:{run_id}:{target_id}:{action}:{idempotency_suffix}",
        f"upload_page_{action}",
        "upload_page",
        run_id,
        "file_manifest" if target_id != run_id else "run",
        target_id,
        before,
        after,
        {"source": "upload_page"},
        "ok",
        "",
    )


def _is_manifest_done(fields: dict) -> bool:
    status = _ftext(fields.get("状态"))
    return status in ("已提交", "已解析", "已确认无数据", "已关闭")


def _status_badge(fields: dict) -> tuple[str, str]:
    status = _ftext(fields.get("状态")) or "待提交"
    if _is_manifest_done(fields):
        return status, "done"
    if status in ("待补充", "P0待补件"):
        return status, "blocked"
    return status, "pending"


def _manifest_row_html(rec: dict, run_id: str, token: str) -> str:
    f = rec.get("fields", {})
    mid = _ftext(f.get("file_manifest_id"))
    platform = _ftext(f.get("平台"))
    shop = _ftext(f.get("店铺"))
    file_type = _ftext(f.get("文件类型"))
    status_text, status_class = _status_badge(f)
    atts = _manifest_attachments(f)
    files = "<br>".join(html.escape(str(a.get("name") or a.get("file_token") or "附件")) for a in atts) or "<span class=\"hint\">未上传</span>"
    row_class = "done" if status_class == "done" else ("blocked" if status_class == "blocked" else "pending")
    actions = [_upload_form(run_id, token, mid)]
    if file_type == "广告账单":
        actions.append(_action_form(run_id, token, mid, "confirm_no_ad", "确认该店本月无广告消耗", disabled=bool(atts)))
    elif file_type in NO_SETTLEMENT_FILE_TYPES:
        actions.append(_action_form(run_id, token, mid, "confirm_no_settlement", "确认该店本月无结算", disabled=bool(atts)))
    elif file_type == "物流账单":
        actions.append(_action_form(run_id, token, mid, "logistics_missing", "暂缺账单，提交缺口说明", note=True, warn=True))
    if platform in DEFERABLE_SCOPE_PLATFORMS and not _is_manifest_done(f):
        actions.append(_action_form(run_id, token, mid, "defer_platform_scope", "口径未定，本期暂缓此平台", note=True, warn=True))
    actions.append(_action_form(run_id, token, mid, "note", "补充说明", note=True))
    return f"""
<tr class="{row_class}">
  <td data-label="平台">{html.escape(platform)}</td>
  <td data-label="店铺">{html.escape(shop)}</td>
  <td data-label="资料项">{html.escape(file_type)}</td>
  <td data-label="状态"><span class="status {status_class}">{html.escape(status_text)}</span></td>
  <td data-label="已上传/证据"><div class="files">{files}</div></td>
  <td data-label="操作"><div class="actions">{''.join(actions)}</div></td>
</tr>
"""


def _hidden_fields(run_id: str, token: str, file_manifest_id: str) -> str:
    return (
        f'<input type="hidden" name="run_id" value="{html.escape(run_id)}">'
        f'<input type="hidden" name="token" value="{html.escape(token)}">'
        f'<input type="hidden" name="file_manifest_id" value="{html.escape(file_manifest_id)}">'
    )


def _upload_form(run_id: str, token: str, file_manifest_id: str) -> str:
    return (
        '<form class="inline-form upload-form" method="post" enctype="multipart/form-data" action="/upload">'
        f'{_hidden_fields(run_id, token, file_manifest_id)}'
        '<input type="file" name="files" multiple required>'
        '<button type="submit">上传文件</button>'
        '</form>'
    )


def _action_form(run_id: str, token: str, file_manifest_id: str, action_name: str,
                 label: str, *, note: bool = False, warn: bool = False, disabled: bool = False) -> str:
    note_input = '<input type="text" name="note" placeholder="说明，可选">' if note else '<input type="hidden" name="note" value="">'
    disabled_attr = " disabled" if disabled else ""
    klass = "warn" if warn else ""
    return (
        '<form class="inline-form" method="post" action="/upload/action">'
        f'{_hidden_fields(run_id, token, file_manifest_id)}'
        f'<input type="hidden" name="action_name" value="{html.escape(action_name)}">'
        f'{note_input}'
        f'<button class="{klass}" type="submit"{disabled_attr}>{html.escape(label)}</button>'
        '</form>'
    )


async def _legacy_pointer_for_manifest(manifest: dict) -> dict:
    f = manifest.get("fields", {})
    period = _ftext(f.get("月份"))
    platform = _ftext(f.get("平台"))
    shop = _ftext(f.get("店铺"))
    file_type = _ftext(f.get("文件类型"))
    rows = await _legacy_rows(period)
    if file_type in LOGISTICS_LEGACY_FIELD_BY_TYPE:
        old_field = LOGISTICS_LEGACY_FIELD_BY_TYPE[file_type]
        for row in rows:
            if row.get("fields", {}).get("数据类型") == "物流账单":
                return {"record_id": row["record_id"], "field": old_field}
    old_field = SHOP_LEGACY_FIELD_BY_TYPE.get(file_type)
    if old_field:
        for row in rows:
            rf = row.get("fields", {})
            if rf.get("数据类型") != "店铺数据":
                continue
            if _ftext(rf.get("平台")) == platform and _ftext(rf.get("店铺")) == shop:
                return {"record_id": row["record_id"], "field": old_field}
    return {}


async def _mirror_uploaded_files_to_legacy(manifest: dict, uploaded: list[dict]) -> dict:
    pointer = await _legacy_pointer_for_manifest(manifest)
    record_id = pointer.get("record_id")
    field = pointer.get("field")
    if not record_id or not field:
        return {"ok": False, "reason": "legacy_pointer_not_found"}
    existing: list[dict] = []
    try:
        current = await feishu.bitable_get_record(config.TASK_APP_TOKEN, config.TASK_TABLE_ID, record_id)
        current_fields = ((current.get("data") or {}).get("record") or {}).get("fields") or {}
        existing = _attachments(current_fields.get(field))
    except Exception:
        existing = []
    merged = _merge_attachments(existing, uploaded)
    res = await feishu.bitable_update_record(config.TASK_APP_TOKEN, config.TASK_TABLE_ID, record_id, {
        field: merged,
    })
    return {
        "ok": res.get("code") == 0,
        "record_id": record_id,
        "field": field,
        "attachment_count": len(merged),
        "response_code": res.get("code"),
        "response_msg": res.get("msg"),
    }
