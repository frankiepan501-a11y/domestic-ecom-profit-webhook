"""主流程编排 — 接收任务台 record_id，端到端跑完毛利报表."""
import asyncio
import json
import time
import traceback
from datetime import datetime
from . import config, feishu, parsers, engine, writer


# 第一版硬编码 5 SKU 成本 (复制今天领星拉的真实数据)
# 第二版改成调 lingxing.get_products() 自动同步
SKU_COST_FALLBACK = {
    "PK01pro":  96.00,
    "PK02-S2":  164.73,
    "PK02-S3":  170.00,
    "PK02-S2A": 176.73,
    "PK02-S3A": 182.00,
}
SKU_NAME_FALLBACK = {
    "PK01pro":  "switch2 砖块拓展坞",
    "PK02-S2":  "YM24-食人花2代",
    "PK02-S3":  "YM24-食人花2代（左磁吸右滑轨）",
    "PK02-S2A": "YM24-食人花2代+叶子数据线",
    "PK02-S3A": "YM24-食人花2代（左磁吸右滑轨）+叶子数据线",
}


async def update_status(record_id: str, fields: dict):
    """改任务台行状态 + 字段。飞书 PUT 用 field_name 作 key (不是 field_id)."""
    return await feishu.bitable_update_record(
        config.TASK_APP_TOKEN, config.TASK_TABLE_ID, record_id, fields)


async def get_record(record_id: str) -> dict:
    res = await feishu.bitable_get_record(
        config.TASK_APP_TOKEN, config.TASK_TABLE_ID, record_id)
    return res.get("data", {}).get("record", {}).get("fields", {})


async def find_month_sources(year_month: str) -> list[dict]:
    """搜该月份所有"店铺数据"+"物流账单"行."""
    all_records = await feishu.bitable_search_records(
        config.TASK_APP_TOKEN, config.TASK_TABLE_ID)
    out = []
    for r in all_records:
        f = r.get("fields", {})
        # 月份字段值在飞书 bitable 是 [{"text": "..."}] 或 string
        m = f.get("月份")
        if isinstance(m, list) and m:
            m = m[0].get("text", "")
        if m != year_month:
            continue
        dtype = f.get("数据类型")
        # SingleSelect 返回 string
        if dtype not in ("店铺数据", "物流账单"):
            continue
        r["_fields_resolved"] = f
        out.append(r)
    return out


def _attachment_list(field_value) -> list[dict]:
    """飞书附件字段值 = list of {file_token, name, type, ...}."""
    if not field_value:
        return []
    if isinstance(field_value, list):
        return field_value
    return []


async def _download_attachments(record: dict, kind_field: str) -> list[tuple[str, bytes]]:
    """下载某字段的所有附件 → [(filename, bytes)]."""
    f = record.get("_fields_resolved", {})
    fid = config.FIELD_IDS.get(kind_field)  # 用 field_id 拼 extra
    atts = _attachment_list(f.get(kind_field))  # 用 field_name 取值
    out = []
    for a in atts:
        token = a.get("file_token")
        name = a.get("name", "unknown")
        if not token:
            continue
        # bitable 附件下载需要 extra 参数
        extra = json.dumps({
            "bitablePerm": {
                "tableId": config.TASK_TABLE_ID,
                "rev": 0,
                "attachments": {
                    fid: {record["record_id"]: [token]}
                }
            }
        })
        try:
            buf = await feishu.drive_download_media(token, extra=extra)
            out.append((name, buf))
        except Exception as e:
            print(f"  ✗ 下载 {name} 失败: {e}")
    return out


async def collect_raw_data(year_month: str) -> dict:
    """汇总该月所有附件解析后的 raw 数据."""
    sources = await find_month_sources(year_month)
    raw = {"orders": [], "refunds": [], "plat_fees": [], "ads": [], "logistics": [],
           "sku_set": set(), "errors": []}

    for rec in sources:
        f = rec["_fields_resolved"]
        dtype = f.get("数据类型")
        title = f.get("任务标题")
        if isinstance(title, list) and title:
            title = title[0].get("text", "")
        print(f"  → 处理: {title}")

        if dtype == "店铺数据":
            for kind, attach_field in [("订单", "订单明细"), ("退款", "退款明细"),
                                       ("平台费", "平台费用"), ("广告", "广告/推广")]:
                files = await _download_attachments(rec, attach_field)
                for fname, buf in files:
                    print(f"    解析 [{kind}] {fname} ({len(buf)} bytes)")
                    res = parsers.detect_and_parse(fname, buf, year_month, kind)
                    if res["kind"] == "error":
                        raw["errors"].append(res["msg"])
                        continue
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
            files = await _download_attachments(rec, "物流月结账单")
            for fname, buf in files:
                print(f"    解析 [物流] {fname} ({len(buf)} bytes)")
                res = parsers.detect_and_parse(fname, buf, year_month, "物流")
                if res["kind"] == "error":
                    raw["errors"].append(res["msg"])
                    continue
                raw["logistics"].extend(res["data"])

    return raw


async def run_profit(record_id: str) -> dict:
    """主入口 - 端到端跑完报表."""
    started_at = datetime.now()
    started_ms = int(started_at.timestamp() * 1000)

    try:
        # 1. 改状态 = 计算中
        await update_status(record_id, {
            "任务状态": "计算中",
            "计算开始时间": started_ms,
            "错误日志": "",
        })

        # 2. 读月份
        rec = await get_record(record_id)
        m = rec.get("月份")
        if isinstance(m, list) and m:
            m = m[0].get("text", "")
        if not m:
            raise ValueError("月份字段为空")
        year_month = m
        print(f"=== 跑毛利报表: {year_month} ===")

        # 3. 收集 raw
        raw = await collect_raw_data(year_month)
        print(f"  订单 {len(raw['orders'])} / 退款 {len(raw['refunds'])} / "
              f"平台费 {len(raw['plat_fees'])} / 广告 {len(raw['ads'])} / "
              f"物流 {len(raw['logistics'])} / SKU {len(raw['sku_set'])}")

        if not raw["orders"]:
            raise ValueError("没找到任何订单数据 — 请确认子任务行已上传附件 + 月份字段填对")

        # 4. SKU 成本 (v0.1 用 fallback, v0.2 接领星)
        sku_costs = {sku: SKU_COST_FALLBACK.get(sku, 0) for sku in raw["sku_set"]}
        sku_names = {sku: SKU_NAME_FALLBACK.get(sku, "") for sku in raw["sku_set"]}

        # 5. 跑 engine
        result = engine.compute(raw["orders"], raw["refunds"], raw["plat_fees"],
                                raw["ads"], raw["logistics"], sku_costs, sku_names)

        # 6. 创建新表 + 写 sheets
        token, sm = await writer.create_report_spreadsheet(year_month)
        url = f"https://u1wpma3xuhr.feishu.cn/sheets/{token}"
        print(f"  报表已建: {url}")

        log_info = result["__logistics__"]
        await writer.write_doc_sheet(token, sm["00_导数说明"], year_month, {
            "generated_at": started_at.strftime("%Y-%m-%d %H:%M"),
            "shops": "POWKONG旗舰店 (v0.1 仅支持此店)",
            "orders": len(raw["orders"]),
            "refunds": len(raw["refunds"]),
            "plat_fees": len(raw["plat_fees"]),
            "ads": len(raw["ads"]),
            "logistics": len(raw["logistics"]),
            "log_hit_rate": f"{log_info['hit']}/{log_info['hit']+log_info['miss']}",
        })
        await writer.write_raw_sheets(token, sm, year_month, raw)
        await writer.write_master_sheets(token, sm, sku_costs, sku_names)
        await writer.write_result_sheets(token, sm, year_month, result)

        # 7. 回填状态 = 已完成
        finished_ms = int(datetime.now().timestamp() * 1000)
        totals = result["__totals__"]
        gross = (totals["total_paid"] - totals["total_refund"] - totals["total_plat"]
                 - totals["total_ad"] - totals["total_cost"] - totals["total_log"])
        net = totals["total_paid"] - totals["total_refund"]
        gross_rate = gross / net * 100 if net else 0

        await update_status(record_id, {
            "任务状态": "✅已完成",
            "计算完成时间": finished_ms,
            "报表飞书链接": {"link": url, "text": f"(AI)国内电商毛利表-{year_month}"},
            "错误日志": (f"销售 {totals['total_paid']:.2f} | 净销售 {net:.2f} | "
                       f"毛利 {gross:.2f} ({gross_rate:.1f}%)"
                       + (f" | 警告: {len(raw['errors'])} 个解析错误" if raw["errors"] else "")),
        })

        # 8. 推 Frankie
        await feishu.send_text(config.FRANKIE_OPEN_ID,
            f"📊 国内电商毛利报表 {year_month} 已生成\n"
            f"店铺: POWKONG旗舰店 (v0.1)\n"
            f"销售额: ¥{totals['total_paid']:.2f}\n"
            f"毛利额: ¥{gross:.2f} ({gross_rate:.1f}%)\n"
            f"物流匹配率: {log_info['hit']}/{log_info['hit']+log_info['miss']}\n"
            f"\n报表链接: {url}")

        return {"ok": True, "url": url, "gross": gross, "gross_rate": gross_rate}

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
