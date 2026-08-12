"""Classify and notify domestic-ecommerce settlement gaps by business owner."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from . import cards, config, feishu, ledger, settlement_engine


OPS_PROBLEM_MARKERS = (
    "无法取得商家编码",
    "商家编码为空",
    "多个商家编码",
    "无法唯一映射采购成本",
)
OPERATIONS_GAP_CATEGORIES = frozenset({"资料缺口", "订单明细匹配", "采购成本", "物流成本"})
PAGE_SIZE = 25


def _text(value: Any) -> str:
    return str(value or "").strip()


def _cost_context(cost_rows: list[list[Any]]) -> tuple[dict[str, list[Any]], dict[str, list[list[Any]]]]:
    by_order: dict[str, list[Any]] = {}
    by_sku: dict[str, list[list[Any]]] = {}
    for row in cost_rows:
        if len(row) < 10:
            continue
        order_id = _text(row[3])
        sku = _text(row[4]).upper()
        if order_id:
            by_order.setdefault(order_id, row)
        if sku:
            by_sku.setdefault(sku, []).append(row)
    return by_order, by_sku


def extract_order_context(raw: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Read settlement-ledger order facts needed to make operations cards actionable."""
    out: dict[str, dict[str, str]] = {}
    for source in raw.get("source_files") or []:
        platform = _text(source.get("platform"))
        fname = _text(source.get("fname") or source.get("name"))
        eligible = (
            (platform == "天猫" and "交易货款" in fname)
            or (platform == "抖音" and "结算订单" in fname)
            or (platform == "小红书" and "商品结算明细" in fname)
        )
        if not eligible:
            continue
        buf = source.get("buf") or b""
        if fname.lower().endswith(".csv"):
            rows = settlement_engine.read_csv(buf, prefer_gbk=platform == "天猫")
        elif fname.lower().endswith((".xlsx", ".xls")):
            sheet = "商品结算明细" if platform == "小红书" else None
            rows = settlement_engine.sheet_rows(buf, fname, sheet) if sheet else settlement_engine.sheet_rows(buf, fname)
        else:
            rows = []
        for row in rows:
            order_id = settlement_engine.norm(
                settlement_engine.p(row, "订单号") or settlement_engine.p(row, "订单编号")
            )
            if not order_id:
                continue
            item = {
                "order_time": settlement_engine.norm(
                    settlement_engine.p(row, "下单时间")
                    or settlement_engine.p(row, "订单创建时间")
                    or settlement_engine.p(row, "订单下单时间")
                    or settlement_engine.p(row, "支付时间")
                    or settlement_engine.p(row, "订单支付时间")
                    or settlement_engine.p(row, "订单成交时间")
                ),
                "settled_time": settlement_engine.norm(
                    settlement_engine.p(row, "确认收货时间") or settlement_engine.p(row, "打款时间")
                    or settlement_engine.p(row, "结算时间") or settlement_engine.p(row, "到账时间")
                ),
                "platform_product_id": settlement_engine.norm(settlement_engine.p(row, "商品ID")),
                "platform_sku": settlement_engine.norm(settlement_engine.p(row, "sku")),
                "name": settlement_engine.norm(settlement_engine.p(row, "商品名称")),
            }
            current = out.setdefault(order_id, {})
            for key, value in item.items():
                if value and not current.get(key):
                    current[key] = value
    return out


def classify_settlement_cost_gaps(
    settlement: dict[str, Any], *, order_context: dict[str, dict[str, str]] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Route all actionable domestic-ecommerce P0 gaps to domestic operations."""
    classified: dict[str, list[dict[str, Any]]] = {
        "operations": [],
        "procurement": [],
        "finance_review": [],
    }
    order_context = order_context or {}
    by_order, by_sku = _cost_context(settlement.get("cost_rows") or [])
    seen: set[tuple[str, str, str, str, str]] = set()
    for row in settlement.get("gap_rows") or []:
        if len(row) < 9 or _text(row[0]) != "P0":
            continue
        platform, shop, month = map(_text, row[1:4])
        gap_category = _text(row[4])
        if gap_category not in OPERATIONS_GAP_CATEGORIES:
            continue
        obj, problem, impact, action = map(_text, row[5:9])
        haystack = f"{problem} {impact} {action}"
        route = "operations"
        order_id = ""
        erp_sku = ""
        name = ""
        source = ""
        if gap_category == "资料缺口":
            object_type = "店铺"
        elif gap_category == "物流成本":
            if any(marker in haystack for marker in ("物流单号为空", "快递单号为空", "未解析到有效运单号")):
                object_type = "订单号"
                order_id = obj
            else:
                object_type = "运单号"
        elif gap_category == "订单明细匹配" or any(marker in haystack for marker in OPS_PROBLEM_MARKERS):
            object_type = "订单号"
            cost_row = by_order.get(obj) or []
            order_id = obj
            erp_sku = _text(cost_row[4]) if len(cost_row) > 4 else ""
            name = _text(cost_row[5]) if len(cost_row) > 5 else ""
            source = _text(cost_row[9]) if len(cost_row) > 9 else ""
        else:
            matches = [
                cost_row for cost_row in (by_sku.get(obj.upper()) or [])
                if len(cost_row) >= 3
                and tuple(map(_text, cost_row[:3])) == (platform, shop, month)
            ]
            if matches:
                object_type = "ERP SKU"
                cost_row = matches[0]
                order_id = _text(cost_row[3]) if len(cost_row) > 3 else ""
                erp_sku = obj.upper()
                name = _text(cost_row[5]) if len(cost_row) > 5 else ""
                source = _text(cost_row[9]) if len(cost_row) > 9 else ""
            else:
                object_type = "待核实对象"
        key = (route, platform, shop, obj, problem)
        if key in seen:
            continue
        seen.add(key)
        classified[route].append({
            "platform": platform,
            "shop": shop,
            "month": month,
            "gap_category": gap_category,
            "object": obj,
            "object_type": object_type,
            "order_id": order_id,
            "erp_sku": erp_sku,
            "name": name,
            "problem": problem,
            "impact": impact,
            "action": action,
            "cost_source": source,
            "order_time": _text((order_context.get(order_id) or {}).get("order_time")),
            "settled_time": _text((order_context.get(order_id) or {}).get("settled_time")),
            "platform_product_id": _text((order_context.get(order_id) or {}).get("platform_product_id")),
            "platform_sku": _text((order_context.get(order_id) or {}).get("platform_sku")),
        })
        if not classified[route][-1]["name"]:
            classified[route][-1]["name"] = _text((order_context.get(order_id) or {}).get("name"))
    return classified


def _alert_key(month: str, route: str, audience: str, entries: list[dict[str, Any]]) -> str:
    facts = sorted(
        f"{x.get('platform')}|{x.get('shop')}|{x.get('object')}|{x.get('problem')}"
        for x in entries
    )
    digest = hashlib.sha1(
        f"{month}:{route}:{audience}:{'|'.join(facts)}".encode("utf-8")
    ).hexdigest()[:32]
    return f"domestic_profit_cost_gap_alert_v4:{route}:{audience}:{digest}"


def _pages(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [entries[i:i + PAGE_SIZE] for i in range(0, len(entries), PAGE_SIZE)]


async def _write_send_audit(month: str, route: str, key: str, entries: list[dict[str, Any]],
                            message_ids: list[str], targets: list[str], channel: str) -> None:
    await ledger.write_audit(
        key,
        "cost_gap_alert_v2",
        "system",
        ledger.run_id_for_month(month),
        "cost_gap",
        route,
        {},
        {"message_ids": message_ids, "targets": targets, "channel": channel},
        {
            "month": month,
            "route": route,
            "objects": [str(x.get("object") or "") for x in entries],
        },
        "sent" if message_ids else "no_recipient",
    )


async def _record_send_without_blocking(
    month: str,
    route: str,
    key: str,
    entries: list[dict[str, Any]],
    message_ids: list[str],
    targets: list[str],
    channel: str,
) -> None:
    try:
        await _write_send_audit(month, route, key, entries, message_ids, targets, channel)
    except Exception as exc:
        print(f"  [WARN] {route} 成本缺口发送审计写入失败: {type(exc).__name__}")


async def _already_sent_or_audit_unavailable(key: str, route: str) -> bool:
    try:
        return await ledger.audit_exists(key)
    except Exception as exc:
        print(f"  [WARN] {route} 成本缺口幂等查询失败，本条暂不发送: {type(exc).__name__}")
        return True


async def send_settlement_cost_gap_alerts(
    month: str,
    settlement: dict[str, Any],
    raw: dict[str, Any],
    *,
    frankie_only: bool,
) -> dict[str, list[str]]:
    """Send domestic-ecommerce gaps to operations, the single accountable owner."""
    classified = classify_settlement_cost_gaps(
        settlement,
        order_context=extract_order_context(raw),
    )
    sent_by_route: dict[str, list[str]] = {
        "operations": [],
        "procurement": [],
        "finance_review": [],
    }
    audience = "frankie_preview" if frankie_only else "production"

    operations = classified["operations"]
    if operations:
        from . import card_workflow

        operation_pages = _pages(operations)
        mentions: dict[str, str] | None = None
        for page_index, page in enumerate(operation_pages, start=1):
            key = _alert_key(month, "operations", audience, page)
            if await _already_sent_or_audit_unavailable(key, "operations"):
                continue
            try:
                if mentions is None:
                    mentions = {} if frankie_only else await card_workflow._ops_group_mentions()
                run_id = ledger.run_id_for_month(month)
                upload_url = (
                    f"{config.PUBLIC_BASE_URL}/upload?run_id={run_id}"
                    f"&token={ledger.upload_token(run_id, 'run')}"
                )
                card = cards.cost_gap_alert_card(
                    month,
                    "operations",
                    page,
                    mention_open_ids=list(mentions),
                    action_url=upload_url,
                    all_entries=operations,
                    page_index=page_index,
                    page_count=len(operation_pages),
                )
                mids, targets, channel = await card_workflow._send_ops_card(
                    card,
                    frankie_only=frankie_only,
                )
            except Exception as exc:
                print(f"  [WARN] 运营成本缺口卡发送失败: {type(exc).__name__}")
                continue
            sent_by_route["operations"].extend(mids)
            if mids:
                await _record_send_without_blocking(
                    month, "operations", key, page, mids, targets, channel
                )

    return sent_by_route


def _existing_group_message_ids(audits: list[dict[str, Any]]) -> list[str]:
    """Return distinct operation-gap group-card message ids, oldest first."""
    found: list[tuple[int, str]] = []
    for rec in audits:
        fields = rec.get("fields") or {}
        if (
            _text(fields.get("action")) != "cost_gap_alert_v2"
            or _text(fields.get("target_id")) != "operations"
            or _text(fields.get("result")) != "sent"
        ):
            continue
        try:
            after = json.loads(ledger.extract_text(fields.get("after_json")) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if _text(after.get("channel")) != "group":
            continue
        created_raw = ledger.extract_text(fields.get("created_at"))
        try:
            created_at = int(float(created_raw or 0))
        except ValueError:
            try:
                created_at = int(datetime.fromisoformat(created_raw.replace("Z", "+00:00")).timestamp() * 1000)
            except (TypeError, ValueError):
                created_at = 0
        for message_id in after.get("message_ids") or []:
            if _text(message_id):
                found.append((created_at, _text(message_id)))
    ordered: list[str] = []
    for _, message_id in sorted(found, key=lambda item: item[0]):
        if message_id not in ordered:
            ordered.append(message_id)
    return ordered


async def refresh_existing_operation_gap_cards(
    month: str,
    settlement: dict[str, Any],
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Patch existing production gap cards after a silent rerun; never send a new card."""
    from . import card_workflow

    run_id = ledger.run_id_for_month(month)
    audits = await ledger.find_many(ledger.AUDIT_TABLE, "run_id", run_id)
    existing_message_ids = _existing_group_message_ids(audits)
    operations = classify_settlement_cost_gaps(
        settlement,
        order_context=extract_order_context(raw),
    )["operations"]
    pages = _pages(operations)

    active_count = min(len(existing_message_ids), len(pages))
    active_message_ids = existing_message_ids[-active_count:] if active_count else []
    invalidated_message_ids = existing_message_ids[:-active_count] if active_count else list(existing_message_ids)
    failed: list[dict[str, str]] = []
    patched_active: list[str] = []
    patched_invalid: list[str] = []

    mentions: dict[str, str] = {}
    if pages and active_message_ids:
        mentions = await card_workflow._ops_group_mentions()
    upload_url = (
        f"{config.PUBLIC_BASE_URL}/upload?run_id={run_id}"
        f"&token={ledger.upload_token(run_id, 'run')}"
    )
    for page_index, (message_id, page) in enumerate(
        zip(active_message_ids, pages[-active_count:]),
        start=max(1, len(pages) - active_count + 1),
    ):
        card = cards.cost_gap_alert_card(
            month,
            "operations",
            page,
            mention_open_ids=list(mentions),
            action_url=upload_url,
            all_entries=operations,
            page_index=page_index,
            page_count=len(pages),
        )
        try:
            response = await feishu.patch_message_card(message_id, card, use_event_app=True)
            if response.get("code") == 0:
                patched_active.append(message_id)
            else:
                failed.append({"message_id": message_id, "reason": _text(response.get("msg") or response.get("code"))})
        except Exception as exc:
            failed.append({"message_id": message_id, "reason": f"{type(exc).__name__}: {exc}"})

    invalid_card = cards.processed_card(
        "⚠️ 本卡无效，请勿操作",
        "系统已用现有附件重新初检；请以群内最新一张缺口卡为准。",
        ok=False,
        details={"月份": month, "处理": "旧卡停用，避免重复补件"},
    )
    for message_id in invalidated_message_ids:
        try:
            response = await feishu.patch_message_card(message_id, invalid_card, use_event_app=True)
            if response.get("code") == 0:
                patched_invalid.append(message_id)
            else:
                failed.append({"message_id": message_id, "reason": _text(response.get("msg") or response.get("code"))})
        except Exception as exc:
            failed.append({"message_id": message_id, "reason": f"{type(exc).__name__}: {exc}"})

    missing_existing_cards = max(0, len(pages) - len(active_message_ids))
    audit_key = (
        f"cost-gap-refresh:{run_id}:"
        f"{hashlib.sha1(ledger.compact_json(operations, limit=20000).encode('utf-8')).hexdigest()[:20]}"
    )
    await ledger.finalize_audit(
        audit_key,
        "cost_gap_card_refresh",
        "system",
        run_id,
        "cost_gap",
        "operations",
        {"existing_message_ids": existing_message_ids},
        {
            "active_message_ids": patched_active,
            "invalidated_message_ids": patched_invalid,
            "missing_existing_cards": missing_existing_cards,
            "failed": failed,
        },
        {"month": month, "gap_count": len(operations)},
        "patched" if not failed and missing_existing_cards == 0 else "partial",
    )
    return {
        "run_id": run_id,
        "gap_count": len(operations),
        "active_message_ids": patched_active,
        "invalidated_message_ids": patched_invalid,
        "missing_existing_cards": missing_existing_cards,
        "failed": failed,
    }
