"""Classify and notify settlement procurement-cost gaps by business owner."""
from __future__ import annotations

import hashlib
from typing import Any

from . import cards, config, feishu, ledger, settlement_engine


OPS_PROBLEM_MARKERS = (
    "无法取得商家编码",
    "商家编码为空",
    "多个商家编码",
    "无法唯一映射采购成本",
)
PROCUREMENT_PROBLEM_MARKERS = (
    "采购成本表未匹配",
    "成本为0",
)
PROCUREMENT_DEPT_ROOTS = ["od-273719791eed9b0558c20e0960da991a"]
PROCUREMENT_JOB_TITLES = ["采购专员"]
PAGE_SIZE = 25


def _text(value: Any) -> str:
    return str(value or "").strip()


def _has_zero_unit_cost(row: list[Any]) -> bool:
    if len(row) <= 7:
        return False
    try:
        return float(row[7] or 0) <= 0
    except (TypeError, ValueError):
        return False


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
    """Return P0 procurement-cost gaps grouped by the person who can actually fix them."""
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
        if gap_category not in {"采购成本", "订单明细匹配"}:
            continue
        obj, problem, impact, action = map(_text, row[5:9])
        haystack = f"{problem} {impact} {action}"
        if gap_category == "订单明细匹配" or any(marker in haystack for marker in OPS_PROBLEM_MARKERS):
            route = "operations"
            object_type = "订单号"
            cost_row = by_order.get(obj) or []
            order_id = obj
            erp_sku = _text(cost_row[4]) if len(cost_row) > 4 else ""
            name = _text(cost_row[5]) if len(cost_row) > 5 else ""
            source = _text(cost_row[9]) if len(cost_row) > 9 else ""
        elif any(marker in haystack for marker in PROCUREMENT_PROBLEM_MARKERS):
            matches = [row for row in (by_sku.get(obj.upper()) or []) if _has_zero_unit_cost(row)]
            if matches:
                route = "procurement"
                object_type = "ERP SKU"
                cost_row = matches[0]
                order_id = _text(cost_row[3]) if len(cost_row) > 3 else ""
                erp_sku = obj.upper()
                name = _text(cost_row[5]) if len(cost_row) > 5 else ""
                source = _text(cost_row[9]) if len(cost_row) > 9 else ""
            else:
                route = "finance_review"
                object_type = "待判断对象"
                order_id = ""
                erp_sku = ""
                name = ""
                source = ""
        else:
            route = "finance_review"
            object_type = "待判断对象"
            order_id = ""
            erp_sku = ""
            name = ""
            source = ""
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
    return f"domestic_profit_cost_gap_alert_v3:{route}:{audience}:{digest}"


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
    """Send each cost gap only to the role that can fix it."""
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

    procurement = classified["procurement"]
    if procurement:
        procurement_pages = _pages(procurement)
        if frankie_only:
            targets = {config.FRANKIE_OPEN_ID: "潘志聪"}
            target_audience = "frankie_preview"
        else:
            try:
                targets = await feishu.resolve_users_by_job_title(
                    PROCUREMENT_DEPT_ROOTS,
                    PROCUREMENT_JOB_TITLES,
                )
            except Exception as exc:
                print(f"  [WARN] 采购岗位解析失败，改发 Frankie 兜底: {type(exc).__name__}")
                targets = {}
            if targets:
                target_audience = "production"
            else:
                targets = {config.FRANKIE_OPEN_ID: "潘志聪"}
                target_audience = "routing_fallback"
        for page_index, page in enumerate(procurement_pages, start=1):
            card = cards.cost_gap_alert_card(
                month,
                "procurement",
                page,
                action_url=config.COST_TABLE_URL,
                all_entries=procurement,
                page_index=page_index,
                page_count=len(procurement_pages),
            )
            for open_id, target_name in targets.items():
                key = _alert_key(
                    month,
                    "procurement",
                    f"{target_audience}:{open_id}",
                    page,
                )
                if await _already_sent_or_audit_unavailable(key, "procurement"):
                    continue
                try:
                    res = await feishu.send_interactive_open_id(
                        open_id,
                        card,
                        use_event_app=False,
                    )
                except Exception as exc:
                    print(f"  [WARN] 采购成本缺口卡发送失败: {type(exc).__name__}")
                    continue
                mid = (res.get("data") or {}).get("message_id")
                if not mid:
                    continue
                sent_by_route["procurement"].append(mid)
                await _record_send_without_blocking(
                    month,
                    "procurement",
                    key,
                    page,
                    [mid],
                    [target_name],
                    "private",
                )

    finance_review = classified["finance_review"]
    if finance_review:
        finance_pages = _pages(finance_review)
        for page_index, page in enumerate(finance_pages, start=1):
            key = _alert_key(month, "finance_review", audience, page)
            if await _already_sent_or_audit_unavailable(key, "finance_review"):
                continue
            card = cards.cost_gap_alert_card(
                month,
                "finance_review",
                page,
                all_entries=finance_review,
                page_index=page_index,
                page_count=len(finance_pages),
            )
            try:
                res = await feishu.send_interactive_open_id(
                    config.FRANKIE_OPEN_ID,
                    card,
                    use_event_app=False,
                )
            except Exception as exc:
                print(f"  [WARN] 待判断成本缺口卡发送失败: {type(exc).__name__}")
                continue
            mid = (res.get("data") or {}).get("message_id")
            mids = [mid] if mid else []
            sent_by_route["finance_review"].extend(mids)
            if mids:
                await _record_send_without_blocking(
                    month,
                    "finance_review",
                    key,
                    page,
                    mids,
                    ["潘志聪"],
                    "private",
                )

    return sent_by_route
