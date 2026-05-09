"""创建新飞书电子表格 + 写 13 sheets + 公式 — 复用今天的逻辑."""
from datetime import date
from . import feishu


# ===== 13 sheets 定义 (与今天的飞书表完全一致) =====
SHEET_DEFS = [
    ("00_导数说明", 200, 4),
    ("01_订单明细_导入", 1000, 22),
    ("02_退款明细_导入", 500, 18),
    ("03_平台费用_导入", 1000, 13),
    ("04_广告佣金_导入", 500, 15),
    ("05_物流月结账单_导入", 2000, 13),
    ("06_ERP_SKU成本表", 500, 13),
    ("07_物流成本规则表", 200, 8),
    ("08_产品主数据", 500, 16),
    ("09_数据源台账", 100, 10),
    ("10_毛利结果表", 1000, 28),
    ("11_店铺汇总看板", 200, 13),
    ("12_异常预警", 500, 9),
]

HEADERS = {
    "01_订单明细_导入": ['月份','平台','店铺','订单号','子订单号','下单时间','付款时间','商品ID','平台SKU ID','SKU编码/商家编码(=ERP_SKU)','产品名称','SKU属性/规格','数量','商品原价','买家实付金额','平台补贴金额','店铺优惠金额','运费收入','快递单号','订单状态','退款状态','备注'],
    "02_退款明细_导入": ['月份','平台','店铺','退款单号','原订单号','子订单号','SKU编码/商家编码(=ERP_SKU)','产品名称','退款完成时间','退款金额','退货数量','售后类型','售后原因','商家承担金额','平台承担金额','是否可二次销售','备注'],
    "03_平台费用_导入": ['月份','平台','店铺','订单号','SKU编码/商家编码(=ERP_SKU)','费用日期','费用类型','费用金额','费用归属层级','是否商家承担','分摊规则','备注'],
    "04_广告佣金_导入": ['月份','平台','店铺','广告/佣金渠道','计划/达人/联盟名称','商品ID','SKU编码/商家编码(=ERP_SKU)','日期','花费/佣金金额','成交金额','成交订单数','归属层级','分摊规则','备注'],
    "05_物流月结账单_导入": ['月份','快递公司','运单号','发件日期','寄件地区','到件地区','计费重量','应付金额','折扣','服务类型','经手人/运单发放(参考)','来源文件','备注'],
    "06_ERP_SKU成本表": ['ERP_SKU(=商家编码)','标准产品ID','标准产品名称','规格型号','采购成本(含包材)','默认物流成本','其他单件成本','成本生效日期','成本失效日期','成本负责人','成本数据来源','备注'],
    "07_物流成本规则表": ['规则编号','平台','区域','重量段(kg)','默认运费(元)','包材费(元)','规则来源','备注'],
    "08_产品主数据": ['ERP_SKU(=商家编码)','标准产品ID','标准产品名称','平台','店铺','平台商品ID','平台SKU ID','类目','品牌','产品类型','是否主推','产品负责人','目标毛利率','毛利预警线','备注'],
    "09_数据源台账": ['平台','店铺','数据维度(订单/退款/平台费用/广告/物流)','数据源类型(报表导出/API)','文件名 或 API 端点','鉴权方式','测试状态','上次更新时间','负责对接人','备注'],
    "10_毛利结果表": ['月份','平台','店铺','ERP_SKU(=商家编码)','标准产品名称','品牌','产品负责人','销量','退款数量','净销量','销售额','平台佣金','基础软件服务费','营销托管费','消费券代扣','其他平台费','平台费合计','推广/广告费','退款金额','采购成本(含包材)','物流成本','物流来源标记','净销售额','毛利额','毛利率','售后损耗占比','平台费占比'],
    "11_店铺汇总看板": ['平台','店铺','品牌','SKU 数','总销量','净销量','销售额','净销售额','平台费合计','广告费合计','采购成本','物流成本','毛利额','毛利率'],
    "12_异常预警": ['异常类型','严重度','平台','店铺','ERP_SKU/单号','描述','影响金额','处理建议'],
}


async def create_report_spreadsheet(year_month: str) -> tuple[str, dict[str, str]]:
    """创建新飞书电子表格 + 13 sheets + 写表头。返回 (token, sheet_id_map)."""
    title = f"(AI)国内电商毛利表-{year_month.replace('-', '/')}"
    res = await feishu.sheets_create(title)
    token = res["data"]["spreadsheet"]["spreadsheet_token"]

    # 加 Frankie full_access
    from . import config
    await feishu.perm_add_collaborator(token, "sheet", config.FRANKIE_OPEN_ID, "full_access")

    # 看默认 sheet
    meta = await feishu.sheets_metainfo(token)
    default_sid = meta["data"]["sheets"][0]["sheetId"]

    # 改默认为 00 + 加其他 12 个
    ops = [{"updateSheet": {"properties": {"sheetId": default_sid, "title": SHEET_DEFS[0][0]}}}]
    for i, (name, _, _) in enumerate(SHEET_DEFS[1:], start=1):
        ops.append({"addSheet": {"properties": {"title": name, "index": i}}})
    await feishu.sheets_batch_update(token, ops)

    # 重新拿 metainfo
    meta = await feishu.sheets_metainfo(token)
    sm = {s["title"]: s["sheetId"] for s in meta["data"]["sheets"]}

    # 批量写表头
    value_ranges = []
    for sheet_name, headers in HEADERS.items():
        sid = sm[sheet_name]
        end_col = _col_letter(len(headers))
        value_ranges.append({"range": f"{sid}!A1:{end_col}1", "values": [headers]})
    await feishu.sheets_values_batch_update(token, value_ranges)

    return token, sm


def _col_letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


async def write_doc_sheet(token: str, sid: str, year_month: str, summary: dict):
    """写 00_导数说明 (简化版,只放本月报表元信息)."""
    rows = [
        [f"📊 国内电商毛利报表 — {year_month}", ""],
        ["", ""],
        ["报表月份", year_month],
        ["生成时间", summary.get("generated_at", "")],
        ["覆盖店铺", summary.get("shops", "POWKONG旗舰店")],
        ["", ""],
        ["【数据规模】", ""],
        ["订单行数", summary.get("orders", 0)],
        ["退款行数", summary.get("refunds", 0)],
        ["平台费用行数", summary.get("plat_fees", 0)],
        ["广告行数", summary.get("ads", 0)],
        ["物流月结行数", summary.get("logistics", 0)],
        ["", ""],
        ["【映射逻辑】", ""],
        ["SKU 主键", "01-04 表的 SKU编码/商家编码 = 06.ERP_SKU = 08.ERP_SKU"],
        ["物流归属", "05.运单号 ⟵join⟶ 01.快递单号 → 订单号 → SKU"],
        ["物流命中率", summary.get("log_hit_rate", "—")],
        ["", ""],
        ["【毛利公式】", "毛利额 = 净销售额 - 平台费合计 - 广告费 - 采购成本(含包材) - 物流成本"],
    ]
    await feishu.sheets_values_put(token, sid, 1, rows)


async def write_raw_sheets(token: str, sm: dict, year_month: str, raw: dict):
    """写 01-05 raw 数据 sheets."""
    PLATFORM = "天猫"
    SHOP = "POWKONG旗舰店"

    # 01 订单明细
    orders = []
    for o in raw["orders"]:
        orders.append([year_month, PLATFORM, SHOP, o["main_oid"], o["sub_oid"],
                       o["create_t"], o["pay_t"], "", "",
                       o["sku"], o["title"], o["attr"], o["qty"], o["price"], o["paid"],
                       "", "", "", o["tracking"], o["status"], o["refund_status"], ""])
    await _batch_write(token, sm["01_订单明细_导入"], orders)

    # 02 退款
    refunds = []
    for r in raw["refunds"]:
        refunds.append([year_month, PLATFORM, SHOP, r["refund_id"], r["main_oid"], "",
                        r["sku"], r["title"], r["complete_t"], r["amount"], "",
                        r["type"], r["reason"], r["to_buyer"], r["to_platform"], "", ""])
    await _batch_write(token, sm["02_退款明细_导入"], refunds)

    # 03 平台费
    pf = []
    for f in raw["plat_fees"]:
        pf.append([year_month, PLATFORM, SHOP, "", "", year_month, f["fee_type"], f["amount"],
                   "店铺", "是", "按店铺销售额分摊到SKU", f["source"]])
    await _batch_write(token, sm["03_平台费用_导入"], pf)

    # 04 广告
    ads = []
    for a in raw["ads"]:
        ads.append([year_month, PLATFORM, SHOP, "天猫推广", a["channel_name"], "", "",
                    a["date"], a["spend"], a["sales"], a["orders"],
                    "店铺", "按店铺/SKU销售额分摊", "推广明细.csv"])
    await _batch_write(token, sm["04_广告佣金_导入"], ads)

    # 05 物流
    log_rows = []
    for L in raw["logistics"]:
        log_rows.append([year_month, L["carrier"], L["tracking"], L["date"],
                         L["from"], L["to"], L["weight"], L["amount"], L["discount"],
                         L["service_type"], L["operator"], "n8n 自动导入", ""])
    await _batch_write(token, sm["05_物流月结账单_导入"], log_rows)


async def write_master_sheets(token: str, sm: dict, sku_costs: dict, sku_names: dict):
    """写 06_ERP_SKU成本表 + 08_产品主数据."""
    PLATFORM = "天猫"
    SHOP = "POWKONG旗舰店"
    today = str(date.today())

    sku_rows = []
    prod_rows = []
    for sku in sorted(sku_costs.keys()):
        sku_rows.append([sku, "", sku_names.get(sku, ""), "",
                         sku_costs[sku], "", "",
                         today, "", "(领星自动同步)",
                         "领星 ERP API /erp/sc/data/local_inventory/productList",
                         f"领星 cg_price={sku_costs[sku]}元（已含包材）"])
        prod_rows.append([sku, "", sku_names.get(sku, ""), PLATFORM, SHOP, "", "", "",
                          "POWKONG", "", "", "赵伟俊", "", "", ""])
    await _batch_write(token, sm["06_ERP_SKU成本表"], sku_rows)
    await _batch_write(token, sm["08_产品主数据"], prod_rows)


async def write_result_sheets(token: str, sm: dict, year_month: str, result: dict,
                              extra_alerts: list | None = None):
    """写 10_毛利结果表 (含公式) + 11_店铺汇总看板 + 12_异常预警."""
    PLATFORM = "天猫"
    SHOP = "POWKONG旗舰店"
    sku_data = sorted([(k, v) for k, v in result.items() if not k.startswith("__")],
                      key=lambda x: -x[1]["paid"])

    # 10 毛利结果表 (公式驱动)
    rows10 = []
    for sku, d in sku_data:
        row = len(rows10) + 2
        rows10.append([
            year_month, PLATFORM, SHOP, sku, d["name"][:40], "POWKONG", "赵伟俊",
            d["qty"], d["refund_qty"],
            {"type": "formula", "text": f"=H{row}-I{row}"},
            round(d["paid"], 2),
            round(d["plat_L"], 2), round(d["plat_M"], 2), round(d["plat_N"], 2),
            round(d["plat_O"], 2), round(d["plat_P"], 2),
            {"type": "formula", "text": f"=L{row}+M{row}+N{row}+O{row}+P{row}"},
            round(d["ad"], 2), round(d["refund_amt"], 2),
            {"type": "formula",
             "text": f"=IFERROR(VLOOKUP(D{row},06_ERP_SKU成本表!A:E,5,FALSE)*H{row},0)"},
            round(d["log_amt"], 2),
            f"真实账单 ({d['log_matched']}/{d['log_matched']+d['log_unmatched']} 单)",
            {"type": "formula", "text": f"=K{row}-S{row}"},
            {"type": "formula", "text": f"=W{row}-Q{row}-R{row}-T{row}-U{row}"},
            {"type": "formula", "text": f"=IFERROR(X{row}/W{row},0)"},
            {"type": "formula", "text": f"=IFERROR(S{row}/K{row},0)"},
            {"type": "formula", "text": f"=IFERROR(Q{row}/W{row},0)"},
        ])
    await _batch_write(token, sm["10_毛利结果表"], rows10)

    # 11 看板
    totals = result["__totals__"]
    qty_total = sum(d["qty"] for _, d in sku_data)
    refund_qty_total = sum(d["refund_qty"] for _, d in sku_data)
    sum11 = [[
        PLATFORM, SHOP, "POWKONG", len(sku_data), qty_total,
        qty_total - refund_qty_total,
        round(totals["total_paid"], 2),
        round(totals["total_paid"] - totals["total_refund"], 2),
        round(totals["total_plat"], 2),
        round(totals["total_ad"], 2),
        round(totals["total_cost"], 2),
        round(totals["total_log"], 2),
        round(totals["total_paid"] - totals["total_refund"] - totals["total_plat"]
              - totals["total_ad"] - totals["total_cost"] - totals["total_log"], 2),
        f"{(1 - (totals['total_plat']+totals['total_ad']+totals['total_cost']+totals['total_log']) / max(0.01, totals['total_paid']-totals['total_refund']))*100:.1f}%",
    ]]
    await _batch_write(token, sm["11_店铺汇总看板"], sum11)

    # 12 异常
    alerts = []
    for sku, d in sku_data:
        if d["paid"] > 0 and d["refund_amt"] / d["paid"] > 0.20:
            alerts.append(["高退款率", "中", PLATFORM, SHOP, sku,
                           f"退款 {d['refund_amt']:.2f} / 销售 {d['paid']:.2f} = {d['refund_amt']/d['paid']*100:.1f}%",
                           round(d["refund_amt"], 2), "排查产品质量/描述/物流"])
        if d["net_sales"] > 0 and d["gross_rate"] < 0.05:
            alerts.append(["低毛利", "高", PLATFORM, SHOP, sku,
                           f"毛利率 {d['gross_rate']*100:.1f}% < 5%",
                           round(d["gross"], 2), "考虑涨价/降成本/砍 SKU"])
    log_info = result["__logistics__"]
    alerts.append(["物流匹配率", "正常" if log_info["miss"] == 0 else "中",
                   PLATFORM, SHOP, "(店铺级)",
                   f"成交订单运单 {log_info['hit']}/{log_info['hit']+log_info['miss']} 命中月结",
                   0, "无需处理" if log_info["miss"] == 0 else "排查未匹配运单"])
    if extra_alerts:
        alerts.extend(extra_alerts)
    if alerts:
        await _batch_write(token, sm["12_异常预警"], alerts)


async def _batch_write(token: str, sid: str, rows: list, max_cells: int = 3000):
    if not rows:
        return
    cols = len(rows[0])
    batch = max(1, max_cells // cols)
    written = 0
    while written < len(rows):
        chunk = rows[written:written + batch]
        await feishu.sheets_values_put(token, sid, 2 + written, chunk)
        written += len(chunk)
