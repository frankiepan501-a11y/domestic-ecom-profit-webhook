"""创建新飞书电子表格 + 写 13 sheets + 公式 - v0.2 多店铺多行."""
from datetime import date
from . import feishu, config


# ===== 13 sheets 定义 =====
SHEET_DEFS = [
    ("00_导数说明", 200, 4),
    ("01_订单明细_导入", 5000, 22),
    ("02_退款明细_导入", 1000, 18),
    ("03_平台费用_导入", 1000, 13),
    ("04_广告佣金_导入", 1000, 15),
    ("05_物流月结账单_导入", 5000, 13),
    ("06_ERP_SKU成本表", 500, 13),
    ("07_物流成本规则表", 200, 8),
    ("08_产品主数据", 500, 16),
    ("09_数据源台账", 200, 10),
    ("10_毛利结果表", 1000, 28),
    ("11_店铺汇总看板", 200, 14),
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


def _col(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


async def _grant_report_collaborators(token: str) -> dict:
    """给月度报表 sheet 授权: 显式个人(优先, 覆盖) + 部门成员(实时解析, view)。
    单个失败不阻断建表。每月重跑→部门成员变动自动同步。"""
    ok, fail, fails = 0, 0, []
    done: set[str] = set()
    # 1. 显式个人 (Frankie full_access / 吴晓丹 edit) — 优先级最高, 先授
    for oid, perm in config.REPORT_GRANT_USERS:
        try:
            r = await feishu.perm_add_collaborator(token, "sheet", oid, perm)
            if r.get("code") == 0:
                ok += 1
            else:
                fail += 1; fails.append(f"user {oid}: {r.get('code')} {r.get('msg')}")
        except Exception as e:
            fail += 1; fails.append(f"user {oid}: {e}")
        done.add(oid)
    # 2. 部门成员 (含子部门, 实时解析) → view, 跳过已授个人
    try:
        members = await feishu.resolve_dept_member_openids(config.REPORT_GRANT_DEPT_ROOTS)
    except Exception as e:
        members = {}
        fails.append(f"resolve depts: {e}")
    for oid in members:
        if oid in done:
            continue
        try:
            r = await feishu.perm_add_collaborator(token, "sheet", oid,
                                                   config.REPORT_GRANT_DEPT_PERM)
            if r.get("code") == 0:
                ok += 1
            else:
                fail += 1; fails.append(f"dept {oid}: {r.get('code')} {r.get('msg')}")
        except Exception as e:
            fail += 1; fails.append(f"dept {oid}: {e}")
        done.add(oid)
    summary = {"granted": ok, "failed": fail, "members": len(done)}
    if fails:
        print(f"  ⚠️ 报表授权部分失败 {summary}: {fails[:5]}")
    else:
        print(f"  ✓ 报表授权完成 {summary}")
    return summary


async def create_report_spreadsheet(year_month: str) -> tuple[str, dict[str, str]]:
    title = f"(AI)国内电商毛利表-{year_month.replace('-', '/')}"
    res = await feishu.sheets_create(title)
    token = res["data"]["spreadsheet"]["spreadsheet_token"]

    await _grant_report_collaborators(token)

    meta = await feishu.sheets_metainfo(token)
    default_sid = meta["data"]["sheets"][0]["sheetId"]

    ops = [{"updateSheet": {"properties": {"sheetId": default_sid, "title": SHEET_DEFS[0][0]}}}]
    for i, (name, _, _) in enumerate(SHEET_DEFS[1:], start=1):
        ops.append({"addSheet": {"properties": {"title": name, "index": i}}})
    await feishu.sheets_batch_update(token, ops)

    meta = await feishu.sheets_metainfo(token)
    sm = {s["title"]: s["sheetId"] for s in meta["data"]["sheets"]}

    # 批量写表头
    value_ranges = []
    for sn, hdrs in HEADERS.items():
        sid = sm[sn]
        value_ranges.append({"range": f"{sid}!A1:{_col(len(hdrs))}1", "values": [hdrs]})
    await feishu.sheets_values_batch_update(token, value_ranges)

    return token, sm


async def write_doc_sheet(token: str, sid: str, year_month: str, summary: dict):
    rows = [
        [f"📊 国内电商毛利报表 — {year_month}", ""],
        ["", ""],
        ["报表月份", year_month],
        ["生成时间", summary.get("generated_at", "")],
        ["覆盖店铺数", summary.get("shop_count", 0)],
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
        ["", ""],
        ["【毛利公式】", "毛利额 = 净销售额 - 平台费合计 - 广告费 - 采购成本(含包材) - 物流成本"],
        ["成本来源", "领星 ERP API /erp/sc/data/local_inventory/productList (cg_price 字段, 自动同步)"],
        ["", ""],
        ["【店铺解析支持 v0.2】", ""],
        ["天猫 (POWKONG/纷岚)", "✅ 完整支持"],
        ["抖音", "⏳ v0.2 P3 待加"],
        ["小红书", "⏳ v0.2 P4 待加"],
        ["京东", "⏳ v0.2 P5 待加"],
    ]
    await feishu.sheets_values_put(token, sid, 1, rows)


async def write_raw_sheets(token: str, sm: dict, year_month: str, raw: dict):
    """写 01-05 raw, 每行带 platform/shop 标签."""
    # 01 订单
    orders = []
    for o in raw["orders"]:
        orders.append([year_month, o["platform"], o["shop"],
                       o.get("main_oid", ""), o.get("sub_oid", ""),
                       o.get("create_t", ""), o.get("pay_t", ""), "", "",
                       o.get("sku", ""), o.get("title", ""), o.get("attr", ""),
                       o.get("qty", 0), o.get("price", 0), o.get("paid", 0),
                       "", "", "", o.get("tracking", ""),
                       o.get("status", ""), o.get("refund_status", ""), ""])
    await _batch_write(token, sm["01_订单明细_导入"], orders)

    # 02 退款
    refunds = []
    for r in raw["refunds"]:
        refunds.append([year_month, r["platform"], r["shop"],
                        r.get("refund_id", ""), r.get("main_oid", ""), "",
                        r.get("sku", ""), r.get("title", ""), r.get("complete_t", ""),
                        r.get("amount", 0), r.get("qty", ""),
                        r.get("type", ""), r.get("reason", ""),
                        r.get("to_buyer", 0), r.get("to_platform", 0), "", ""])
    await _batch_write(token, sm["02_退款明细_导入"], refunds)

    # 03 平台费 (店铺月度汇总)
    pf = []
    for f in raw["plat_fees"]:
        pf.append([year_month, f["platform"], f["shop"], "", "", year_month,
                   f.get("fee_type", ""), f.get("amount", 0),
                   "店铺", "是", "按店铺销售额分摊到SKU", f.get("source", "")])
    await _batch_write(token, sm["03_平台费用_导入"], pf)

    # 04 广告
    ads = []
    for a in raw["ads"]:
        ads.append([year_month, a["platform"], a["shop"],
                    a.get("channel", a["platform"] + "推广"),
                    a.get("channel_name", ""), "", "",
                    a.get("date", ""), a.get("spend", 0), a.get("sales", 0),
                    a.get("orders", 0), "店铺", "按店铺/SKU销售额分摊", a.get("source", "")])
    await _batch_write(token, sm["04_广告佣金_导入"], ads)

    # 05 物流
    logs = []
    for L in raw["logistics"]:
        logs.append([year_month, L.get("carrier", ""), L.get("tracking", ""),
                     L.get("date", ""), L.get("from", ""), L.get("to", ""),
                     L.get("weight", 0), L.get("amount", 0), L.get("discount", 0),
                     L.get("service_type", ""), L.get("operator", ""),
                     L.get("source", "n8n 自动"), ""])
    await _batch_write(token, sm["05_物流月结账单_导入"], logs)


async def write_master_sheets(token: str, sm: dict, sku_costs: dict[str, float],
                              sku_meta: dict[str, dict], sku_to_shops: dict[str, set]):
    """写 06_ERP_SKU 成本表 + 08_产品主数据."""
    today = str(date.today())
    sku_rows = []
    prod_rows = []
    for sku in sorted(sku_costs.keys()):
        meta = sku_meta.get(sku, {})
        cost = sku_costs[sku]
        sku_rows.append([sku, "", meta.get("name", ""), "",
                         cost, "", "",
                         today, "", "(领星自动同步)",
                         "领星 ERP API /erp/sc/data/local_inventory/productList",
                         f"cg_price={cost}元 (含包材)"])
        # 一个 SKU 可能在多店铺出现 — 取第一个 shop 作为主数据(或 list)
        shops = sorted(sku_to_shops.get(sku, set()))
        ps = shops[0] if shops else ("", "")
        prod_rows.append([sku, "", meta.get("name", ""),
                          ps[0] if isinstance(ps, tuple) else "",
                          ps[1] if isinstance(ps, tuple) else "",
                          "", "", meta.get("category", ""), meta.get("brand", ""),
                          "", "", "赵伟俊", "", "",
                          f"在 {len(shops)} 个店铺销售" if len(shops) > 1 else ""])
    await _batch_write(token, sm["06_ERP_SKU成本表"], sku_rows)
    await _batch_write(token, sm["08_产品主数据"], prod_rows)


async def write_result_sheets(token: str, sm: dict, year_month: str, result: dict,
                              extra_alerts: list | None = None):
    """写 10 / 11 / 12. v0.2 多店铺多行."""
    by_sku = result["by_sku"]
    shop_totals = result["shop_totals"]
    shop_log = result["shop_log_stat"]

    # 排序: 按店铺销售额降序, 店铺内按 SKU 销售额降序
    shop_paid_rank = sorted(shop_totals.items(), key=lambda x: -x[1]["paid"])
    shop_order = [sk for sk, _ in shop_paid_rank]

    rows10 = []
    for shop_key in shop_order:
        platform, shop = shop_key
        # 该店铺的所有 SKU
        sku_in_shop = [(k, v) for k, v in by_sku.items() if (k[0], k[1]) == shop_key]
        sku_in_shop.sort(key=lambda x: -x[1]["paid"])
        for key, d in sku_in_shop:
            row = len(rows10) + 2
            sku = key[2]
            rows10.append([
                year_month, platform, shop, sku, d["name"][:40],
                _brand_of(sku), "赵伟俊",
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
                f"真实账单 ({d['log_matched']}/{d['log_matched']+d['log_unmatched']})",
                {"type": "formula", "text": f"=K{row}-S{row}"},
                {"type": "formula", "text": f"=W{row}-Q{row}-R{row}-T{row}-U{row}"},
                {"type": "formula", "text": f"=IFERROR(X{row}/W{row},0)"},
                {"type": "formula", "text": f"=IFERROR(S{row}/K{row},0)"},
                {"type": "formula", "text": f"=IFERROR(Q{row}/W{row},0)"},
            ])
    await _batch_write(token, sm["10_毛利结果表"], rows10)

    # 11 店铺汇总
    rows11 = []
    for shop_key in shop_order:
        platform, shop = shop_key
        st = shop_totals[shop_key]
        net = st["paid"] - st["refund_amt"]
        gross = net - st["plat"] - st["ad"] - st["cost"] - st["log_amt"]
        gross_rate = gross / net if net else 0
        rows11.append([
            platform, shop, _shop_brand(shop),
            st["sku_count"], st["qty"], st["qty"] - st["refund_qty"],
            round(st["paid"], 2), round(net, 2),
            round(st["plat"], 2), round(st["ad"], 2),
            round(st["cost"], 2), round(st["log_amt"], 2),
            round(gross, 2), f"{gross_rate*100:.1f}%",
        ])
    await _batch_write(token, sm["11_店铺汇总看板"], rows11)

    # 12 异常预警
    alerts = []
    for key, d in by_sku.items():
        if d["paid"] > 0 and d["refund_amt"] / d["paid"] > 0.20:
            alerts.append(["高退款率", "中", key[0], key[1], key[2],
                           f"退款 {d['refund_amt']:.2f}/销售 {d['paid']:.2f} = "
                           f"{d['refund_amt']/d['paid']*100:.1f}%",
                           round(d["refund_amt"], 2), "排查产品/描述/物流"])
        if d["net_sales"] > 0 and d["gross_rate"] < 0.05:
            alerts.append(["低毛利", "高", key[0], key[1], key[2],
                           f"毛利率 {d['gross_rate']*100:.1f}% < 5%",
                           round(d["gross"], 2), "考虑涨价/降成本"])
    # 物流匹配率(按店铺)
    for shop_key, ls in shop_log.items():
        platform, shop = shop_key
        total = ls["hit"] + ls["miss"]
        if total:
            rate = ls["hit"] / total * 100
            sev = "正常" if ls["miss"] == 0 else ("低" if rate > 90 else "中")
            alerts.append(["物流匹配率", sev, platform, shop, "(店铺级)",
                           f"成交订单运单 {ls['hit']}/{total} 命中月结 ({rate:.1f}%)",
                           0, "无需处理" if ls["miss"] == 0 else "排查未匹配运单"])
    if extra_alerts:
        alerts.extend(extra_alerts)
    if alerts:
        await _batch_write(token, sm["12_异常预警"], alerts)


_BRAND_PREFIX = {
    "PK": "POWKONG",
    "FF": "FUNLAB",
}


def _brand_of(sku: str) -> str:
    if not sku or len(sku) < 2:
        return ""
    return _BRAND_PREFIX.get(sku[:2].upper(), "")


def _shop_brand(shop: str) -> str:
    if "POWKONG" in shop or "宝空" in shop:
        return "POWKONG"
    if "纷岚" in shop or "FUNLAB" in shop.upper():
        return "FUNLAB"
    if "正方体" in shop:
        return "混合(FUNLAB/POWKONG/白牌)"
    return ""


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
