"""主流程 v0.2: 多店铺三维聚合 + 领星 API 同步成本.

v0.2.0 关键变化:
- 领星 cg_price 自动同步替代硬编码 fallback
- engine + writer 支持 (平台,店铺,SKU) 三维
- v0.2 P1 仍只跑 POWKONG (parser 限制), P2 加纷岚, P3/P4/P5 加抖音/小红书/京东
"""
import asyncio
import json
import os
import traceback
from datetime import datetime
from . import config, cost_gap_alert, feishu, parsers, engine, writer, lingxing, sf_api, settlement_engine, ledger


# v0.3: 全 9 店铺 (加抖音宝空 + 京东宝空, 京东 parser 重写支持费用流水按订单聚合)
V02_SHOP_WHITELIST = {
    ("天猫", "POWKONG旗舰店"),
    ("天猫", "纷岚店"),
    ("抖音", "纷岚店"),
    ("抖音", "宝空店"),
    ("小红书", "纷岚店"),
    ("小红书", "宝空店"),
    ("拼多多", "正方体电玩店"),
    ("淘宝", "正方体电玩店"),
    ("京东", "京东纷岚店"),
    ("京东", "宝空店"),
}

async def update_status(record_id: str, fields: dict):
    return await feishu.bitable_update_record(
        config.TASK_APP_TOKEN, config.TASK_TABLE_ID, record_id, fields)


async def _notify_report_ready(msg: str):
    """报表生成通知 → 按职务实时查(国内平台运营专员/财务助理/财务部主管) + 显式潘志聪/吴晓丹 (单点失败不阻断)。"""
    targets: dict = {}
    try:
        targets.update(await feishu.resolve_users_jt_fallback(
            config.NOTIFY_JT_DEPT_ROOTS, config.REPORT_NOTIFY_JOB_TITLES))
    except Exception as e:
        print(f"  ⚠️ 解析通知职务失败: {e}")
    for oid in config.REPORT_NOTIFY_EXTRA_USERS:  # 显式加潘志聪/吴晓丹
        targets.setdefault(oid, "")
    sent = 0
    for oid in targets:
        try:
            await feishu.send_text(oid, msg)
            sent += 1
        except Exception as e:
            print(f"  ⚠️ 通知 {oid} 失败: {e}")
    print(f"  ✓ 报表通知已发 {sent}/{len(targets)} 人")


async def get_record(record_id: str) -> dict:
    res = await feishu.bitable_get_record(
        config.TASK_APP_TOKEN, config.TASK_TABLE_ID, record_id)
    return res.get("data", {}).get("record", {}).get("fields", {})


async def find_month_sources(year_month: str) -> list[dict]:
    all_records = await feishu.bitable_search_records(
        config.TASK_APP_TOKEN, config.TASK_TABLE_ID)
    out = []
    for r in all_records:
        f = r.get("fields", {})
        m = f.get("月份")
        if isinstance(m, list) and m:
            m = m[0].get("text", "")
        if m != year_month:
            continue
        if f.get("数据类型") not in ("店铺数据", "物流账单"):
            continue
        r["_fields_resolved"] = f
        out.append(r)
    return out


def _attachment_list(field_value) -> list[dict]:
    if not field_value:
        return []
    if isinstance(field_value, list):
        return field_value
    return []


async def _download_attachments(record: dict, kind_field: str) -> list[tuple[str, bytes]]:
    f = record.get("_fields_resolved", {})
    fid = config.FIELD_IDS.get(kind_field)
    atts = _attachment_list(f.get(kind_field))
    out = []
    for a in atts:
        token = a.get("file_token")
        name = a.get("name", "unknown")
        if not token:
            continue
        extra = json.dumps({
            "bitablePerm": {
                "tableId": config.TASK_TABLE_ID,
                "rev": 0,
                "attachments": {fid: {record["record_id"]: [token]}}
            }
        })
        try:
            buf = await feishu.drive_download_media(token, extra=extra)
            out.append((name, buf))
        except Exception as e:
            print(f"  ✗ 下载 {name}: {e}")
    return out


def _scalar(value) -> str:
    """Extract text/number from common Bitable value shapes."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("name") or item.get("value") or ""))
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    if isinstance(value, dict):
        if "value" in value:
            inner = value.get("value")
            if isinstance(inner, list):
                return "".join(_scalar(x) for x in inner).strip()
            return _scalar(inner)
        return str(value.get("text") or value.get("name") or "")
    return str(value)


def _num(value) -> float:
    import re
    text = _scalar(value).replace(",", "").replace("¥", "").replace("￥", "").replace("元", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group(0)) if m else 0.0


async def _load_finance_cost_map(sku_set: set[str], lx_data: dict[str, dict]) -> dict[str, dict]:
    """Cost priority: finance accounting cost > ERP cost > Lingxing cg_price."""
    rows = await feishu.bitable_search_records(
        config.COST_APP_TOKEN,
        config.COST_TABLE_ID,
        page_size=500,
        field_names=["ERP SKU", "ERP品名", "采购成本(财务核算)", "采购成本(ERP)"],
    )
    cost_map: dict[str, dict] = {}
    for rec in rows:
        f = rec.get("fields", {})
        sku = _scalar(f.get("ERP SKU")).strip().upper()
        if not sku:
            continue
        fin = _num(f.get("采购成本(财务核算)"))
        erp = _num(f.get("采购成本(ERP)"))
        if fin > 0:
            unit = fin
            source = "采购成本(财务核算)"
        elif erp > 0:
            unit = erp
            source = "采购成本(ERP)兜底"
        else:
            unit = 0.0
            source = "成本缺失/为0"
        cost_map[sku] = {
            "unit_cost": unit,
            "name": _scalar(f.get("ERP品名")),
            "source": source,
            "finance_cost": fin,
            "erp_cost": erp,
        }
    for sku in sorted(sku_set):
        key = str(sku or "").strip().upper()
        if not key:
            continue
        current = cost_map.get(key)
        if current and float(current.get("unit_cost") or 0) > 0:
            continue
        info = lx_data.get(key) or lx_data.get(sku) or {}
        lx_cost = float(info.get("cost") or 0)
        if lx_cost > 0:
            cost_map[key] = {
                "unit_cost": lx_cost,
                "name": info.get("name", ""),
                "source": "领星cg_price兜底",
                "finance_cost": 0.0,
                "erp_cost": 0.0,
            }
        elif key not in cost_map:
            cost_map[key] = {
                "unit_cost": 0.0,
                "name": info.get("name", ""),
                "source": "成本缺失/为0",
                "finance_cost": 0.0,
                "erp_cost": 0.0,
            }
    return cost_map


async def collect_raw_data(year_month: str) -> dict:
    """v0.2: 每条数据带 platform/shop 标签. 白名单外店铺记入 skipped."""
    sources = await find_month_sources(year_month)
    raw = {"orders": [], "refunds": [], "plat_fees": [], "ads": [], "logistics": [],
           "source_files": [], "sku_set": set(), "errors": [], "skipped_shops": [],
           "shop_keys": set()}

    for rec in sources:
        f = rec["_fields_resolved"]
        dtype = f.get("数据类型")
        platform = f.get("平台", "")
        shop = f.get("店铺", "")
        title = f.get("任务标题", "")
        if isinstance(title, list) and title:
            title = title[0].get("text", "")

        if dtype == "店铺数据":
            if (platform, shop) not in V02_SHOP_WHITELIST:
                attach_count = sum(1 for k in ["订单明细","退款明细","平台费用","广告/推广"]
                                  if f.get(k))
                raw["skipped_shops"].append({"title": title, "platform": platform,
                                             "shop": shop, "attachments": attach_count})
                print(f"  ⏭ 跳过(v0.2 P2): {title} [{platform}/{shop}]")
                continue
            print(f"  → {title} [{platform}/{shop}]")
            raw["shop_keys"].add((platform, shop))
            for kind, attach_field in [("订单","订单明细"),("退款","退款明细"),
                                       ("平台费","平台费用"),("广告","广告/推广")]:
                files = await _download_attachments(rec, attach_field)
                for fname, buf in files:
                    raw["source_files"].append({
                        "platform": platform,
                        "shop": shop,
                        "kind": kind,
                        "attach_field": attach_field,
                        "fname": fname,
                        "record_id": rec.get("record_id", ""),
                        "buf": buf,
                    })
                    res = parsers.detect_and_parse(fname, buf, year_month, kind, platform=platform)
                    if res["kind"] == "error":
                        raw["errors"].append(res["msg"])
                        continue
                    # 给每行打 platform/shop 标签
                    for row in res["data"]:
                        row["platform"] = platform
                        row["shop"] = shop
                    if kind == "订单":
                        raw["orders"].extend(res["data"])
                        raw["sku_set"].update(res.get("sku_set", []))
                    elif kind == "退款":
                        raw["refunds"].extend(res["data"])
                    elif kind == "平台费":
                        raw["plat_fees"].extend(res["data"])
                    elif kind == "广告":
                        raw["ads"].extend(res["data"])
                    # 京东特殊: "货款明细"/"订单明细" 在平台费字段, 同时也要按订单聚合
                    if (platform == "京东" and kind == "平台费"
                            and ("货款" in fname or "订单" in fname)):
                        ord_res = parsers.detect_and_parse(fname, buf, year_month,
                                                          "订单", platform=platform)
                        if ord_res["kind"] != "error":
                            for row in ord_res["data"]:
                                row["platform"] = platform
                                row["shop"] = shop
                            raw["orders"].extend(ord_res["data"])
                            raw["sku_set"].update(ord_res.get("sku_set", []))
        elif dtype == "物流账单":
            print(f"  → {title} (全公司池)")
            files = await _download_attachments(rec, "物流月结账单")
            for fname, buf in files:
                raw["source_files"].append({
                    "platform": "全平台",
                    "shop": "全公司",
                    "kind": "物流",
                    "attach_field": "物流月结账单",
                    "fname": fname,
                    "record_id": rec.get("record_id", ""),
                    "buf": buf,
                })
                res = parsers.detect_and_parse(fname, buf, year_month, "物流")
                if res["kind"] == "error":
                    raw["errors"].append(res["msg"])
                    continue
                raw["logistics"].extend(res["data"])

    try:
        manifests = await ledger.manifests_for_run(ledger.run_id_for_month(year_month))
        raw["manifest_statuses"] = [
            {
                "platform": ledger.extract_text(rec.get("fields", {}).get("平台")),
                "shop": ledger.extract_text(rec.get("fields", {}).get("店铺")),
                "file_type": ledger.extract_text(rec.get("fields", {}).get("文件类型")),
                "status": ledger.extract_text(rec.get("fields", {}).get("状态")),
            }
            for rec in manifests
        ]
    except Exception as exc:
        raw["manifest_statuses"] = []
        raw["errors"].append(f"资料清单状态读取失败: {type(exc).__name__}: {exc}")
    return raw


async def sync_settlement_p0_gaps(year_month: str, settlement: dict) -> dict:
    """Make calculated P0s part of the same ledger gate used by finance cards."""
    run_id = ledger.run_id_for_month(year_month)
    gap_type_map = {
        "资料缺口": "其他",
        "订单明细匹配": "ERP_SKU缺失",
        "采购成本": "ERP_SKU缺失",
        "物流成本": "物流账单缺失",
    }
    current_ids: set[str] = set()
    for row in settlement.get("gap_rows", []):
        if len(row) < 9 or str(row[0]).strip() != "P0":
            continue
        platform, shop, month, category, obj, problem, impact, action = [str(x or "").strip() for x in row[1:9]]
        evidence = f"[初检计算] {shop}｜对象：{obj or shop}｜问题：{problem}｜影响：{impact}｜补件：{action}"
        gap = await ledger.create_gap(
            run_id,
            gap_type_map.get(category, "其他"),
            platform,
            month or year_month,
            evidence,
        )
        gap_id = ledger.extract_text(gap.get("fields", {}).get("gap_id"))
        if gap_id:
            current_ids.add(gap_id)
            await ledger.mark_gap(gap_id, {"处理结果": "待处理", "是否可定稿": False})
    for gap in await ledger.gaps_for_run(run_id):
        fields = gap.get("fields", {})
        gap_id = ledger.extract_text(fields.get("gap_id"))
        if ledger.extract_text(fields.get("证据")).startswith("[初检计算]") and gap_id not in current_ids:
            await ledger.mark_gap(gap_id, {"处理结果": "已关闭", "是否可定稿": True})
    return {"run_id": run_id, "open_calculated_p0": len(current_ids), "gap_ids": sorted(current_ids)}


async def run_profit(
    record_id: str,
    *,
    suppress_notify: bool | None = None,
    initial_check_only: bool = False,
) -> dict:
    started_at = datetime.now()
    started_ms = int(started_at.timestamp() * 1000)
    notifications_suppressed = (
        True
        if initial_check_only
        else suppress_notify
        if suppress_notify is not None
        else os.getenv("REPORT_SUPPRESS_NOTIFY", "").strip() not in ("", "0", "false", "False")
    )

    try:
        if not initial_check_only:
            await update_status(record_id, {
                "任务状态": "计算中",
                "计算开始时间": started_ms,
                "错误日志": "",
            })

        rec = await get_record(record_id)
        m = rec.get("月份")
        if isinstance(m, list) and m:
            m = m[0].get("text", "")
        if not m:
            raise ValueError("月份字段为空")
        year_month = m
        print(f"=== v0.2 跑毛利报表: {year_month} ===")

        # 1. 收集 raw (带 platform/shop 标签)
        raw = await collect_raw_data(year_month)
        print(f"  订单 {len(raw['orders'])} / 退款 {len(raw['refunds'])} / "
              f"平台费 {len(raw['plat_fees'])} / 广告 {len(raw['ads'])} / "
              f"物流 {len(raw['logistics'])} / SKU {len(raw['sku_set'])} / "
              f"原始附件 {len(raw['source_files'])} / 跳过店铺 {len(raw['skipped_shops'])}")

        if not raw["orders"] and not raw["source_files"]:
            raise ValueError("没找到任何订单数据或原始附件")

        # 结算文件驱动需要订单明细覆盖结算订单的实际下单区间；旧 parser 会按付款月过滤，
        # 这里从原始文件池补齐 SKU/顺丰候选，避免结算月跨月订单被漏掉。
        try:
            raw["sku_set"].update(settlement_engine.extract_skus(raw, year_month))
        except Exception as e:
            print(f"  ⚠️ 从原始附件提取 SKU 失败: {e}")

        # 2. 调领星 API 拉所有 SKU 成本
        try:
            print(f"  调领星 API 拉 {len(raw['sku_set'])} SKU 成本兜底...")
            lx_data = await lingxing.get_products(raw["sku_set"])
            print(f"  领星拉到 {len(lx_data)} SKU 成本")
        except Exception as e:
            print(f"  ✗ 领星 API 失败: {e}, 继续只用财务成本台")
            lx_data = {}

        try:
            print("  读取产品采购成本台：采购成本(财务核算) > 采购成本(ERP) > 领星cg_price")
            cost_map = await _load_finance_cost_map(raw["sku_set"], lx_data)
            sku_costs = {sku: info.get("unit_cost", 0) for sku, info in cost_map.items()}
            sku_meta = {
                sku: {"name": info.get("name", ""), "cost": info.get("unit_cost", 0), "source": info.get("source", "")}
                for sku, info in cost_map.items()
            }
            print(f"  成本台可用 SKU {len(cost_map)}")
        except Exception as e:
            print(f"  ✗ 产品采购成本台读取失败: {e}, 降级使用领星成本")
            cost_map = {
                sku: {"unit_cost": info.get("cost", 0), "name": info.get("name", ""), "source": "领星cg_price兜底"}
                for sku, info in lx_data.items()
            }
            sku_costs = {sku: info.get("unit_cost", 0) for sku, info in cost_map.items()}
            sku_meta = {
                sku: {"name": info.get("name", ""), "cost": info.get("unit_cost", 0), "source": info.get("source", "")}
                for sku, info in cost_map.items()
            }

        sku_names = {sku: info.get("name", "") for sku, info in sku_meta.items()}

        # SKU → shops 映射 (用于 08 主数据)
        sku_to_shops: dict = {}
        for o in raw["orders"]:
            sku = o.get("sku", "")
            if sku:
                sku_to_shops.setdefault(sku, set()).add((o["platform"], o["shop"]))

        # 2.4 顺丰 API 反查运费 (v0.5 加, 2026-05-12)
        # 策略: 从订单提取所有 SF 运单号 → 并发查 API → 与 xlsx 解析的 logistics 双路径
        #       API 数据 source='API', xlsx 数据 source='xlsx', engine 不区分按 tracking join
        #       同一 tracking 重复时 xlsx 在前 (parser 已加入), API 后到 → 后插入的覆盖
        if config.SF_API_ENABLED:
            sf_trackings = sorted({
                str(o["tracking"]).strip()
                for o in raw["orders"]
                if o.get("tracking") and str(o["tracking"]).strip().startswith("SF")
            })
            try:
                sf_trackings = sorted(set(sf_trackings) | settlement_engine.extract_sf_waybills(raw, year_month))
            except Exception as e:
                print(f"  ⚠️ 从原始附件提取顺丰候选失败: {e}")
            if sf_trackings:
                print(f"  调顺丰 API 反查 {len(sf_trackings)} 运单运费...")
                try:
                    sf_ok, sf_err = await sf_api.query_many(sf_trackings, concurrency=5)
                    raw["logistics"].extend(sf_ok)
                    print(f"  顺丰 API: {len(sf_ok)} 命中 / {len(sf_err)} 失败")
                    if sf_err:
                        # 头 3 条错误样本打日志
                        for e in sf_err[:3]:
                            print(f"    ⚠️ {e['tracking']}: {e['_error']}")
                        raw["sf_api_errors"] = sf_err
                except Exception as e:
                    print(f"  ✗ 顺丰 API 整批失败: {e}, 继续走 xlsx 路径")
                    raw["sf_api_errors"] = [{"_batch_error": str(e)}]

        # 3. 跑旧 engine 作兼容输出，同时跑结算文件驱动 engine 作为财务签核口径
        result = engine.compute(raw["orders"], raw["refunds"], raw["plat_fees"],
                                raw["ads"], raw["logistics"], sku_costs, sku_names,
                                year_month=year_month)
        settlement = settlement_engine.compute(raw, cost_map, year_month)
        await sync_settlement_p0_gaps(year_month, settlement)
        settlement_cost_gap_count = sum(
            1 for g in settlement.get("gap_rows", [])
            if len(g) >= 7
            and g[0] == "P0"
            and g[4] in cost_gap_alert.OPERATIONS_GAP_CATEGORIES
            and str(g[5]).strip()
        )
        refreshed: dict = {}
        refresh_error = ""
        if notifications_suppressed:
            try:
                refreshed = await cost_gap_alert.refresh_existing_operation_gap_cards(
                    year_month,
                    settlement,
                    raw,
                )
                print(
                    "  ✓ 静默重跑仅更新原缺口卡: "
                    f"有效卡 {len(refreshed['active_message_ids'])} / "
                    f"停用旧卡 {len(refreshed['invalidated_message_ids'])} / "
                    f"未找到可更新原卡 {refreshed['missing_existing_cards']}"
                )
            except Exception as e:
                refresh_error = f"{type(e).__name__}: {e}"
                print(f"  ✗ 静默更新原缺口卡失败: {type(e).__name__}: {e}")
        elif settlement_cost_gap_count:
            print(f"  ⚠️ 财务签核口径成本/订单资料 P0 {settlement_cost_gap_count} 个 → 按责任分流")
            try:
                alert_result = await cost_gap_alert.send_settlement_cost_gap_alerts(
                    year_month,
                    settlement,
                    raw,
                    frankie_only=config.COST_GAP_ALERT_FRANKIE_ONLY,
                )
                print(
                    "  ✓ 成本缺口分流卡: "
                    f"运营 {len(alert_result['operations'])} / "
                    f"采购 {len(alert_result['procurement'])} / "
                    f"待财务判断 {len(alert_result['finance_review'])}"
                )
            except Exception as e:
                print(f"  成本缺口分流卡失败: {e}")
        else:
            raw_zero_cost = [
                s for s in raw["sku_set"]
                if s and sku_costs.get(s, 0) == 0 and sku_to_shops.get(s)
            ]
            if raw_zero_cost:
                print(
                    f"  ⏭ 原始订单附件有 {len(raw_zero_cost)} 个成本为0的SKU，但结算签核口径无采购成本P0，"
                    "不发送即时告警"
                )

        if initial_check_only:
            run_id = ledger.run_id_for_month(year_month)
            open_p0 = await ledger.open_p0_gaps(run_id)
            refresh_complete = (
                not refresh_error
                and not refreshed.get("failed")
                and int(refreshed.get("missing_existing_cards") or 0) == 0
            )
            next_status = "P0待补件" if open_p0 else "待AI试算"
            await ledger.update_run(
                run_id,
                next_status,
                "silent_initial_check",
                f"使用运营现有附件静默重跑初检；当前P0缺口 {len(open_p0)} 个；未生成新报表或新卡片",
            )
            return {
                "ok": refresh_complete,
                "mode": "initial_check_only",
                "year_month": year_month,
                "run_id": run_id,
                "open_p0": len(open_p0),
                "calculated_p0": settlement_cost_gap_count,
                "gap_rows": settlement.get("gap_rows", []),
                "card_refresh": refreshed,
                "card_refresh_error": refresh_error,
                "created_report": False,
                "sent_new_card": False,
                "error": "" if refresh_complete else "初检已完成，但原缺口卡未全部更新；未发送新卡片",
            }

        # 4. 创建新表
        token, sm = await writer.create_report_spreadsheet(year_month)
        url = f"https://u1wpma3xuhr.feishu.cn/sheets/{token}"
        print(f"  报表已建: {url}")

        await writer.write_doc_sheet(token, sm["00_导数说明"], year_month, {
            "generated_at": started_at.strftime("%Y-%m-%d %H:%M"),
            "shop_count": len(raw["shop_keys"]),
            "orders": len(raw["orders"]),
            "refunds": len(raw["refunds"]),
            "plat_fees": len(raw["plat_fees"]),
            "ads": len(raw["ads"]),
            "logistics": len(raw["logistics"]),
        })
        await writer.write_raw_sheets(token, sm, year_month, raw)
        await writer.write_master_sheets(token, sm, sku_costs, sku_meta, sku_to_shops)

        extra = []
        if raw.get("skipped_shops"):
            shops_txt = ", ".join(f"{s['platform']}/{s['shop']}" for s in raw["skipped_shops"])
            extra.append(["v0.2 P2 边界 - 解析器待扩展", "提示", "多平台", shops_txt, "(店铺级)",
                         f"v0.2 P2 仅支持天猫(POWKONG+纷岚). 跳过 {len(raw['skipped_shops'])} 店铺",
                         0, "P3 抖音 / P4 小红书 / P5 京东"])
        await writer.write_result_sheets(token, sm, year_month, result, extra_alerts=extra)
        await writer.write_settlement_sheets(token, sm, settlement)

        # 5. 回填
        finished_ms = int(datetime.now().timestamp() * 1000)

        # 全店汇总：优先用结算文件驱动的财务签核口径
        if settlement.get("monthly_rows"):
            all_paid = sum(float(r[6] or 0) for r in settlement["monthly_rows"])
            all_refund = sum(float(r[7] or 0) for r in settlement["monthly_rows"])
            net = sum(float(r[8] or 0) for r in settlement["monthly_rows"])
            all_gross = sum(float(r[14] or 0) for r in settlement["monthly_rows"])
            gross_rate = all_gross / net * 100 if net else 0
        else:
            all_paid = sum(st["paid"] for st in result["shop_totals"].values())
            all_refund = sum(st["refund_amt"] for st in result["shop_totals"].values())
            all_gross = sum(st["paid"] - st["refund_amt"] - st["plat"] - st["ad"]
                            - st["cost"] - st["log_amt"]
                            for st in result["shop_totals"].values())
            net = all_paid - all_refund
            gross_rate = all_gross / net * 100 if net else 0

        await update_status(record_id, {
            "任务状态": "✅已完成",
            "计算完成时间": finished_ms,
            "报表飞书链接": {"link": url, "text": f"国内电商毛利表-{year_month}"},
            "错误日志": (f"结算文件驱动 | 店铺{len(set(r[2] for r in settlement.get('monthly_rows', [])) or raw['shop_keys'])} | 销售{all_paid:.2f} | "
                       f"净销售{net:.2f} | 毛利{all_gross:.2f} ({gross_rate:.1f}%)"
                       + (f" | 跳过{len(raw['skipped_shops'])}店" if raw["skipped_shops"] else "")),
        })

        # 6. 通知 (规范化标题 🟡 [FIN·P2]) → Frankie + 财务部 + 国内电商平台部 成员私聊
        skip_txt = ""
        if raw.get("skipped_shops"):
            shops = ", ".join(s["shop"] for s in raw["skipped_shops"])
            skip_txt = f"\n⏭ 跳过 {len(raw['skipped_shops'])} 店铺: {shops}"

        msg = (
            f"🟡 [FIN·P2] 国内电商毛利报表 · {year_month}\n"
            f"覆盖店铺: {len(raw['shop_keys'])}\n"
            f"销售额: ¥{all_paid:.2f}\n"
            f"净销售: ¥{net:.2f}\n"
            f"毛利额: ¥{all_gross:.2f} ({gross_rate:.1f}%)"
            + skip_txt
            + f"\n\n报表: {url}"
            + f"\n云盘位置: {config.DOMESTIC_ECOM_REPORT_FOLDER_PATH}"
            + f"\n云盘文件夹: {config.DOMESTIC_ECOM_REPORT_FOLDER_URL}"
            + f"\n原始资料: {config.DOMESTIC_ECOM_SOURCE_ARCHIVE_DESC}"
            + "\n📌 请注意查收")
        # REPORT_SUPPRESS_NOTIFY=1 时静默重生(修复重跑用), 不打扰收件人; 缺省/0 正常通知。
        if notifications_suppressed:
            print("  ⏭ 通知已抑制 (REPORT_SUPPRESS_NOTIFY)")
        else:
            await _notify_report_ready(msg)

        return {"ok": True, "url": url, "gross": all_gross, "gross_rate": gross_rate}

    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()[:1500]}"
        print(f"❌ {err_msg}")
        if not initial_check_only:
            try:
                await update_status(record_id, {
                    "任务状态": "❌失败",
                    "错误日志": err_msg[:500],
                })
            except Exception:
                pass
        return {"ok": False, "error": err_msg}
