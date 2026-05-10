"""主流程 v0.2: 多店铺三维聚合 + 领星 API 同步成本.

v0.2.0 关键变化:
- 领星 cg_price 自动同步替代硬编码 fallback
- engine + writer 支持 (平台,店铺,SKU) 三维
- v0.2 P1 仍只跑 POWKONG (parser 限制), P2 加纷岚, P3/P4/P5 加抖音/小红书/京东
"""
import asyncio
import json
import traceback
from datetime import datetime
from . import config, feishu, parsers, engine, writer, lingxing


# v0.2 P4.5: 加拼多多正方体. P5 加京东
V02_SHOP_WHITELIST = {
    ("天猫", "POWKONG旗舰店"),
    ("天猫", "纷岚店"),
    ("抖音", "纷岚店"),
    ("小红书", "纷岚店"),
    ("小红书", "宝空店"),
    ("拼多多", "正方体电玩店"),
}


async def update_status(record_id: str, fields: dict):
    return await feishu.bitable_update_record(
        config.TASK_APP_TOKEN, config.TASK_TABLE_ID, record_id, fields)


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


async def collect_raw_data(year_month: str) -> dict:
    """v0.2: 每条数据带 platform/shop 标签. 白名单外店铺记入 skipped."""
    sources = await find_month_sources(year_month)
    raw = {"orders": [], "refunds": [], "plat_fees": [], "ads": [], "logistics": [],
           "sku_set": set(), "errors": [], "skipped_shops": [], "shop_keys": set()}

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
        elif dtype == "物流账单":
            print(f"  → {title} (全公司池)")
            files = await _download_attachments(rec, "物流月结账单")
            for fname, buf in files:
                res = parsers.detect_and_parse(fname, buf, year_month, "物流")
                if res["kind"] == "error":
                    raw["errors"].append(res["msg"])
                    continue
                raw["logistics"].extend(res["data"])

    return raw


async def run_profit(record_id: str) -> dict:
    started_at = datetime.now()
    started_ms = int(started_at.timestamp() * 1000)

    try:
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
              f"跳过店铺 {len(raw['skipped_shops'])}")

        if not raw["orders"]:
            raise ValueError("没找到任何订单数据")

        # 2. 调领星 API 拉所有 SKU 成本
        try:
            print(f"  调领星 API 拉 {len(raw['sku_set'])} SKU 成本...")
            lx_data = await lingxing.get_products(raw["sku_set"])
            sku_costs = {sku: info["cost"] for sku, info in lx_data.items()}
            sku_meta = lx_data
            missing = raw["sku_set"] - set(sku_costs.keys())
            if missing:
                print(f"  ⚠️ 领星未找到 SKU: {missing}")
            print(f"  领星拉到 {len(sku_costs)} SKU 成本")
        except Exception as e:
            print(f"  ✗ 领星 API 失败: {e}, 用 0 成本占位")
            sku_costs = {sku: 0 for sku in raw["sku_set"]}
            sku_meta = {}

        sku_names = {sku: info.get("name", "") for sku, info in sku_meta.items()}

        # SKU → shops 映射 (用于 08 主数据)
        sku_to_shops: dict = {}
        for o in raw["orders"]:
            sku = o.get("sku", "")
            if sku:
                sku_to_shops.setdefault(sku, set()).add((o["platform"], o["shop"]))

        # 3. 跑 engine
        result = engine.compute(raw["orders"], raw["refunds"], raw["plat_fees"],
                                raw["ads"], raw["logistics"], sku_costs, sku_names)

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

        # 5. 回填
        finished_ms = int(datetime.now().timestamp() * 1000)

        # 全店汇总
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
            "报表飞书链接": {"link": url, "text": f"(AI)国内电商毛利表-{year_month}"},
            "错误日志": (f"店铺{len(raw['shop_keys'])} | 销售{all_paid:.2f} | "
                       f"净销售{net:.2f} | 毛利{all_gross:.2f} ({gross_rate:.1f}%)"
                       + (f" | 跳过{len(raw['skipped_shops'])}店" if raw["skipped_shops"] else "")),
        })

        # 6. 推 Frankie
        skip_txt = ""
        if raw.get("skipped_shops"):
            shops = ", ".join(s["shop"] for s in raw["skipped_shops"])
            skip_txt = f"\n⏭ 跳过 {len(raw['skipped_shops'])} 店铺(待 v0.2 后续 phase): {shops}"

        await feishu.send_text(config.FRANKIE_OPEN_ID,
            f"📊 国内电商毛利报表 {year_month} 已生成 (v0.2.0)\n"
            f"覆盖店铺: {len(raw['shop_keys'])}\n"
            f"销售额: ¥{all_paid:.2f}\n"
            f"净销售: ¥{net:.2f}\n"
            f"毛利额: ¥{all_gross:.2f} ({gross_rate:.1f}%)"
            + skip_txt + f"\n\n报表: {url}")

        return {"ok": True, "url": url, "gross": all_gross, "gross_rate": gross_rate}

    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()[:1500]}"
        print(f"❌ {err_msg}")
        try:
            await update_status(record_id, {
                "任务状态": "❌失败",
                "错误日志": err_msg[:500],
            })
        except Exception:
            pass
        return {"ok": False, "error": err_msg}
