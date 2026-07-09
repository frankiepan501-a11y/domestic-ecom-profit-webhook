"""P0 card-driven monthly workflow for domestic e-commerce profit reports."""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
from datetime import datetime
from typing import Any

from fastapi import UploadFile

from . import cards, config, feishu, ledger, task_runner, task_seeder


SHOP_FILE_FIELDS = [
    ("订单明细", "订单明细", True),
    ("退款明细", "退款明细", True),
    ("平台费用", "平台费用", True),
    ("广告/推广", "广告账单", True),
]
LOGISTICS_FILE_FIELDS = [("物流月结账单", "物流账单", True)]

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
            "workbook_token": "smoke-workbook-token",
            "涉税核对摘要": "smoke: 京东结算月、天猫账期、小红书退款扣减均已过 gate",
            "财务决定": "",
        }
    }, {"fields": {"run_id": run_id, "期间": year_month}}, [])
    sample_cards = {
        "ops_submit": ops_card,
        "p0_gap": gap_card,
        "finance_confirm": finance_card,
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


async def initial_gate_and_maybe_run(run_id: str, period: str) -> dict:
    await _create_missing_gaps(run_id, period)
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
        has_monthly = "产品毛利_月度" in names
        has_quarterly = "产品毛利_季度" in names
    except Exception:
        names = set()
    output = await ledger.create_output(
        run_id,
        workbook_url,
        "P0 gate: ERP_SKU、广告、物流和涉税差异需按缺口台闭环后财务确认。",
        has_monthly=has_monthly,
        has_quarterly=has_quarterly,
    )
    if not has_monthly or not has_quarterly:
        await ledger.update_run(run_id, "试算失败待排查", "finance_gate_blocked",
                                f"workbook 缺少 gate sheet: {sorted(names)}")
        return
    await send_finance_card(run_id, output, frankie_only=config.CARD_WORKFLOW_FRANKIE_ONLY)


async def send_finance_card(run_id: str, output: dict | None = None,
                            *, frankie_only: bool = False) -> dict:
    run = await ledger.find_first(ledger.RUN_TABLE, "run_id", run_id)
    output = output or await ledger.latest_output(run_id)
    if not run or not output:
        return {"sent": [], "error": "missing run/output"}
    gaps = await ledger.gaps_for_run(run_id)
    card = cards.finance_confirm_card(output, run, gaps)
    targets = await _finance_union_targets(frankie_only=frankie_only)
    sent = await _send_card_to_union_targets(card, targets)
    output_id = _ftext(output.get("fields", {}).get("output_id"))
    if sent:
        await ledger.update(ledger.OUTPUT_TABLE, output["record_id"], {"确认卡message_id": ",".join(sent)})
    await ledger.update_run(run_id, "待财务确认", "send_finance_confirm_card",
                            "等待财务在卡片确认/退回/接受临时估算")
    return {"sent": sent, "targets": list(targets.values()), "output_id": output_id}


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
            "action": action,
            "run_id": run_id,
        })
        patch = await _patch_or_reply(ctx["message_id"], ctx["chat_id"], ctx["operator_open_id"], card)
        return {"duplicate": True, "patch": patch}

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
    elif action.startswith("domestic_profit_finance_"):
        output_id = str(value.get("output_id") or "")
        target_type = "output"
        target_id = output_id
        out = await ledger.find_first(ledger.OUTPUT_TABLE, "output_id", output_id) if output_id else None
        if action == "domestic_profit_finance_approve":
            if out:
                await ledger.update(ledger.OUTPUT_TABLE, out["record_id"], {
                    "财务决定": "确认定稿",
                    "财务确认人": ctx["operator_open_id"],
                    "确认时间": ledger.now_ms(),
                })
            await ledger.update_run(run_id, "已归档", action, "财务已确认定稿")
            result_message = "财务已确认定稿，报表运行台已归档。"
        elif action == "domestic_profit_finance_return_data_gap":
            if out:
                await ledger.update(ledger.OUTPUT_TABLE, out["record_id"], {"财务决定": "退回资料缺口"})
            await ledger.update_run(run_id, "财务退回资料缺口", action, "财务退回：资料缺口")
            result_message = "已退回资料缺口，状态回到运营补件链路。"
        elif action == "domestic_profit_finance_return_method_gap":
            if out:
                await ledger.update(ledger.OUTPUT_TABLE, out["record_id"], {"财务决定": "退回口径问题"})
            await ledger.update_run(run_id, "财务退回口径问题", action, "财务退回：口径问题")
            result_message = "已退回口径问题，等待口径修正。"
        elif action == "domestic_profit_finance_accept_temp":
            if out:
                await ledger.update(ledger.OUTPUT_TABLE, out["record_id"], {
                    "财务决定": "接受临时估算",
                    "财务确认人": ctx["operator_open_id"],
                    "确认时间": ledger.now_ms(),
                })
            await ledger.update_run(run_id, "财务接受临时估算", action, "财务接受临时估算旁路终态")
            result_message = "财务已接受临时估算，进入旁路终态。"
    else:
        ok = False
        result_message = f"未知 action: {action}"

    await ledger.write_audit(
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
        details={"action": action, "run_id": run_id, "target": target_id},
    )
    patch = await _patch_or_reply(ctx["message_id"], ctx["chat_id"], ctx["operator_open_id"], processed)
    return {"ok": ok, "action": action, "run_id": run_id, "patch": patch}


async def upload_page(run_id: str, token: str) -> str:
    if token != ledger.upload_token(run_id, "run"):
        return "<h3>Invalid upload token</h3>"
    manifests = await ledger.manifests_for_run(run_id)
    options = []
    for rec in manifests:
        f = rec.get("fields", {})
        mid = html.escape(_ftext(f.get("file_manifest_id")))
        label = html.escape(
            f"{_ftext(f.get('平台'))}/{_ftext(f.get('店铺'))} - {_ftext(f.get('文件类型'))} - {_ftext(f.get('状态'))}"
        )
        if mid:
            options.append(f'<option value="{mid}">{label}</option>')
    if not options:
        options.append('<option value="">未找到资料清单，请先发起 run</option>')
    safe_run = html.escape(run_id)
    return f"""
<!doctype html>
<html><head><meta charset="utf-8"><title>国内电商资料上传</title></head>
<body style="font-family:Arial, sans-serif; max-width:760px; margin:32px auto; line-height:1.5;">
<h2>国内电商毛利报表资料上传</h2>
<p><b>run_id:</b> {safe_run}</p>
<p>选择要补充的资料项并上传。系统会自动写附件台和状态，运营无需进入任务台或 Base 改状态。</p>
<form method="post" enctype="multipart/form-data" action="/upload">
  <input type="hidden" name="run_id" value="{safe_run}">
  <input type="hidden" name="token" value="{html.escape(token)}">
  <p><label>资料项<br><select name="file_manifest_id" style="width:100%; padding:8px;" required>{''.join(options)}</select></label></p>
  <p><label>选择文件<br><input type="file" name="files" multiple required></label></p>
  <p><button type="submit" style="padding:8px 14px;">上传并写入附件台</button></p>
</form>
</body></html>
"""


async def handle_upload(run_id: str, token: str, file_manifest_id: str,
                        files: list[UploadFile]) -> dict:
    if token != ledger.upload_token(run_id, "run"):
        return {"ok": False, "error": "invalid token"}
    manifest = await ledger.find_first(ledger.FILE_TABLE, "file_manifest_id", file_manifest_id)
    if not manifest:
        return {"ok": False, "error": "file_manifest_id not found"}
    uploaded = []
    for f in files:
        content = await f.read()
        res = await feishu.drive_upload_bitable_file(f.filename or "upload.bin", content, config.LEDGER_APP_TOKEN)
        file_token = (res.get("data") or {}).get("file_token")
        if file_token:
            uploaded.append({"file_token": file_token, "name": f.filename})
    if not uploaded:
        return {"ok": False, "error": "no file uploaded"}
    await ledger.mark_manifest(file_manifest_id, {
        "状态": "已提交",
        "附件": uploaded,
        "file_token_json": ledger.compact_json(uploaded),
        "parser结果": "upload_page",
    })
    await ledger.update_run(run_id, "资料初检中", "upload_page", "运营已通过上传页补充资料")
    return {"ok": True, "file_manifest_id": file_manifest_id, "uploaded": uploaded}
