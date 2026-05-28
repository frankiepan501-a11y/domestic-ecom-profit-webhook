"""解析平台报表附件 — 第一版只支持天猫 POWKONG 旗舰店格式."""
import io
import csv
import datetime
from collections import Counter, defaultdict
import openpyxl
import xlrd


def _is_apr(d, year_month: str) -> bool:
    """判断日期是否在指定 YYYY-MM."""
    if d is None:
        return False
    if isinstance(d, datetime.datetime):
        return d.strftime("%Y-%m") == year_month
    return year_month in str(d)


# ===== 天猫订单明细 =====
def parse_tmall_orders(buf: bytes, year_month: str) -> tuple[list[dict], set]:
    """解析天猫订单明细 xlsx → (rows, sku_set)."""
    wb = openpyxl.load_workbook(io.BytesIO(buf), data_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    if not all_rows:
        return [], set()
    header = all_rows[0]
    col = {h: i for i, h in enumerate(header)}
    out = []
    sku_set = set()
    for r in all_rows[1:]:
        pay = r[col.get("订单付款时间", -1)] if "订单付款时间" in col else None
        create = r[col.get("订单创建时间", -1)] if "订单创建时间" in col else None
        t = pay or create
        if not t or year_month not in str(t):
            continue
        sku = r[col.get("商家编码", -1)] or ""
        if sku:
            sku_set.add(sku)
        out.append({
            "main_oid": r[col.get("主订单编号", -1)] or "",
            "sub_oid": r[col.get("子订单编号", -1)] or "",
            "create_t": str(create or ""),
            "pay_t": str(t),
            "sku": sku,
            "title": r[col.get("商品标题", -1)] or "",
            "attr": r[col.get("商品属性", -1)] or "",
            "qty": r[col.get("购买数量", -1)] or 0,
            "price": r[col.get("商品价格", -1)] or 0,
            "paid": r[col.get("买家实付金额", -1)] or 0,
            "tracking": r[col.get("物流单号", -1)] or "",
            "status": r[col.get("订单状态", -1)] or "",
            "refund_status": r[col.get("退款状态", -1)] or "",
        })
    wb.close()
    return out, sku_set


# ===== 天猫退款明细 =====
def parse_tmall_refunds(buf: bytes) -> list[dict]:
    wb = openpyxl.load_workbook(io.BytesIO(buf), data_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    if not all_rows:
        return []
    header = all_rows[0]
    col = {h: i for i, h in enumerate(header) if h}
    out = []
    for r in all_rows[1:]:
        out.append({
            "refund_id": r[col.get("退款编号", -1)] or "",
            "main_oid": r[col.get("订单编号", -1)] or "",
            "sku": r[col.get("商家编码", -1)] or "",
            "title": r[col.get("宝贝标题", -1)] or "",
            "complete_t": str(r[col.get("退款完结时间", -1)] or ""),
            "amount": r[col.get("退款总额", -1)] or 0,
            "type": r[col.get("售后类型", -1)] or "",
            "reason": r[col.get("买家退款原因", -1)] or "",
            "to_buyer": r[col.get("退给买家金额", -1)] or 0,
            "to_platform": r[col.get("退给平台金额", -1)] or 0,
        })
    wb.close()
    return out


# ===== 天猫平台费用 (单文件，月度汇总粒度) =====
def parse_tmall_platform_fee(buf: bytes, source_name: str) -> list[dict]:
    """每个文件 1 行汇总。第 2 列=业务大类(费用类型)，扣费金额列名各异。"""
    wb = openpyxl.load_workbook(io.BytesIO(buf), data_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    if len(all_rows) < 2:
        return []
    header = all_rows[0]
    col = {h: i for i, h in enumerate(header) if h}
    # 扣费金额可能叫 "扣费金额合计 (元）" / "金额" / "支出金额合计（元）" / "捐赠金额" (公益宝贝) 等
    amt_keys = ["扣费金额合计 (元）", "扣费金额合计(元)", "金额", "支出金额合计（元）", "捐赠金额", "本月付款"]
    amt_col = None
    for k in amt_keys:
        if k in col:
            amt_col = col[k]
            break
    fee_type_col = col.get("业务大类", 1)
    out = []
    for r in all_rows[1:]:
        if not r[0]:
            continue
        out.append({
            "fee_type": r[fee_type_col] or "",
            "amount": float(r[amt_col]) if amt_col is not None and r[amt_col] else 0,
            "source": source_name,
        })
    wb.close()
    return out


# ===== 天猫推广明细 csv (GBK 编码) =====
def parse_tmall_ads(buf: bytes) -> list[dict]:
    text = buf.decode("gbk", errors="ignore")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header = rows[0]
    col = {h: i for i, h in enumerate(header)}
    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        out.append({
            "date": r[col.get("日期", 0)],
            "channel_id": r[col.get("场景ID", 1)] if "场景ID" in col else "",
            "channel_name": r[col.get("场景名字", 2)] if "场景名字" in col else "",
            "spend": float(r[col.get("花费", -1)] or 0) if "花费" in col else 0,
            "sales": float(r[col.get("总成交金额", -1)] or 0) if "总成交金额" in col else 0,
            "orders": int(float(r[col.get("总成交笔数", -1)] or 0)) if "总成交笔数" in col else 0,
        })
    return out


# ===== 顺丰月结账单 xlsx (账单明细 sheet) =====
def parse_sf_logistics(buf: bytes, year_month: str) -> list[dict]:
    wb = openpyxl.load_workbook(io.BytesIO(buf), data_only=True)
    if "账单明细" not in wb.sheetnames:
        wb.close()
        return []
    ws = wb["账单明细"]
    rows = list(ws.iter_rows(values_only=True))
    out = []
    # r0=合并标题, r1=表头, r2+ 数据
    for r in rows[2:]:
        if not r[0]:
            continue
        # 顺丰日期格式 "04-01"，需结合 year_month 推年
        date_str = str(r[1]) if r[1] else ""
        if not date_str.startswith(year_month[5:]):  # "04" 月份过滤
            continue
        out.append({
            "carrier": "顺丰",
            "tracking": str(r[2] or "").strip(),
            "date": f"{year_month[:4]}-{date_str}",
            "from": r[3] or "",
            "to": r[4] or "",
            "weight": float(r[6] or 0),
            "amount": float(r[11] or 0),
            "discount": float(r[10] or 0),
            "service_type": r[7] or "",
            "operator": r[12] or "",
        })
    wb.close()
    return out


# ===== 中通月结账单 xlsx =====
def parse_zt_logistics(buf: bytes, year_month: str) -> list[dict]:
    """中通: Sheet1 主账单, 列: 账单日期/运单号/结算重量/金额/.../合计/目的省/目的市/运单发放/结算对象."""
    wb = openpyxl.load_workbook(io.BytesIO(buf), read_only=True, data_only=True)
    ws = wb.active
    out = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r[0]:
            continue
        if not _is_apr(r[0], year_month):
            continue
        out.append({
            "carrier": "中通",
            "tracking": str(r[1] or "").strip(),
            "date": str(r[0])[:10] if r[0] else "",
            "from": "",
            "to": r[6] or "",
            "weight": float(r[2] or 0),
            "amount": float(r[5] or 0),  # 合计
            "discount": 0,
            "service_type": "",
            "operator": r[8] or "",  # 运单发放
        })
    wb.close()
    return out


# ===== 抖音订单 =====
def _strip_tab(v):
    """抖音字段值常带 \\t 前缀."""
    if v is None:
        return ""
    return str(v).strip().lstrip("\t").strip()


def parse_dy_orders(buf: bytes, year_month: str) -> tuple[list[dict], set]:
    """抖音订单 xlsx. 字段: 主订单/子订单/选购商品/商品ID/商家编码/商品数量/商品金额/
    订单提交时间/支付完成时间/订单状态/售后状态/订单类型/订单应付金额/运费/优惠/手续费/商家收入金额."""
    wb = openpyxl.load_workbook(io.BytesIO(buf), data_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    if not all_rows:
        return [], set()
    header = all_rows[0]
    col = {h: i for i, h in enumerate(header)}
    out = []
    sku_set = set()
    for r in all_rows[1:]:
        if not r[0]:
            continue
        pay_t = _strip_tab(r[col.get("支付完成时间", -1)])
        submit_t = _strip_tab(r[col.get("订单提交时间", -1)])
        t = pay_t or submit_t
        if year_month not in t:
            continue
        sku = _strip_tab(r[col.get("商家编码", -1)])
        if sku:
            sku_set.add(sku)
        # v0.7: 从「快递信息」列提取运单号 (格式 "SF0220481613112-顺丰速运,商品..." / "76922..-中通快递,..")
        express = _strip_tab(r[col.get("快递信息", -1)])
        tracking = ""
        if express and express != "-":
            tracking = express.split(",")[0].split("-")[0].strip()
        out.append({
            "main_oid": _strip_tab(r[col.get("主订单编号", -1)]),
            "sub_oid": _strip_tab(r[col.get("子订单编号", -1)]),
            "create_t": submit_t,
            "pay_t": pay_t,
            "sku": sku,
            "title": r[col.get("选购商品", -1)] or "",
            "attr": "",
            "qty": r[col.get("商品数量", -1)] or 0,
            "price": r[col.get("商品金额", -1)] or 0,
            "paid": r[col.get("订单应付金额", -1)] or 0,  # 优惠后实付
            "tracking": tracking,  # v0.7 从快递信息列解析
            "status": r[col.get("订单状态", -1)] or "",
            "refund_status": r[col.get("售后状态", -1)] or "",
        })
    wb.close()
    return out, sku_set


def parse_dy_refunds(buf: bytes) -> list[dict]:
    wb = openpyxl.load_workbook(io.BytesIO(buf), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = rows[0]
    col = {h: i for i, h in enumerate(header) if h}
    out = []
    for r in rows[1:]:
        if not r[0]:
            continue
        out.append({
            "refund_id": _strip_tab(r[col.get("售后单号", -1)]),
            "main_oid": _strip_tab(r[col.get("订单号", -1)]),
            "sku": _strip_tab(r[col.get("商家编码", -1)]),
            "title": r[col.get("商品名称", -1)] or "",
            "complete_t": str(r[col.get("商家退款时间", -1)] or r[col.get("售后完结时间", -1)] or ""),
            "amount": r[col.get("退商品金额（元）", -1)] or 0,
            "type": r[col.get("售后类型", -1)] or "",
            "reason": r[col.get("售后原因", -1)] or "",
            "to_buyer": r[col.get("退商品金额（元）", -1)] or 0,
            "to_platform": 0,
        })
    wb.close()
    return out


def parse_dy_platform_fee(buf: bytes, source_name: str, year_month: str = "") -> list[dict]:
    """抖音平台结算 csv. 只算"出账"且非资金转账类 + 按"动账时间" 过滤当月.
    排除场景: 提现/充值/转账/保证金 (商家自己的资金转移, 不是平台费)."""
    NON_FEE_SCENES = {"提现", "充值", "转账", "保证金", "保证金充值", "保证金退还"}
    text = buf.decode("utf-8-sig", errors="ignore")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header = rows[0]
    col = {h: i for i, h in enumerate(header)}
    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        direction = r[col.get("动账方向", -1)] if "动账方向" in col else ""
        if direction != "出账":
            continue
        scene = r[col.get("动账场景", -1)] or ""
        if scene in NON_FEE_SCENES:
            continue
        t = str(r[col.get("动账时间", -1)] or "") if "动账时间" in col else ""
        if year_month and year_month not in t:
            continue
        out.append({
            "fee_type": scene or "其他",
            "amount": float(r[col.get("动账金额", -1)] or 0),
            "source": source_name,
        })
    return out


# ===== 小红书 =====
def parse_xhs_orders(buf: bytes, year_month: str) -> tuple[list[dict], set]:
    """小红书千帆 75 列订单. 商家编码在 col 69 (按表头查)."""
    wb = openpyxl.load_workbook(io.BytesIO(buf), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], set()
    header = rows[0]
    col = {h: i for i, h in enumerate(header) if h}
    out = []
    sku_set = set()
    for r in rows[1:]:
        if not r[0]:
            continue
        pay_t = str(r[col.get("支付时间", -1)] or "")
        create_t = str(r[col.get("订单创建时间", -1)] or "")
        t = pay_t or create_t
        if year_month not in t:
            continue
        sku = str(r[col.get("商家编码", -1)] or "").strip()
        if sku:
            sku_set.add(sku)
        out.append({
            "main_oid": r[col.get("订单号", -1)] or "",
            "sub_oid": "",
            "create_t": create_t,
            "pay_t": pay_t,
            "sku": sku,
            "title": r[col.get("商品名称", -1)] or "",
            "attr": r[col.get("SKU规格", -1)] or "",
            "qty": r[col.get("SKU件数", -1)] or 0,
            "price": r[col.get("商品总价(元)", -1)] or 0,
            "paid": r[col.get("商家应收金额(元)（支付金额）", -1)] or 0,
            "tracking": str(r[col.get("快递单号", -1)] or "").strip(),
            "status": r[col.get("订单状态", -1)] or "",
            "refund_status": r[col.get("售后状态", -1)] or "",
        })
    wb.close()
    return out, sku_set


def parse_xhs_refunds(buf: bytes) -> list[dict]:
    """小红书退款 — 注意无"商家编码"字段, sku 先留空, P5 后做 main_oid → sku join."""
    wb = openpyxl.load_workbook(io.BytesIO(buf), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = rows[0]
    col = {h: i for i, h in enumerate(header) if h}
    out = []
    for r in rows[1:]:
        if not r[0]:
            continue
        out.append({
            "refund_id": r[col.get("售后单号", -1)] or "",
            "main_oid": r[col.get("订单号", -1)] or "",
            "sku": "",  # 小红书退款表无商家编码
            "title": r[col.get("商品名称", -1)] or "",
            "complete_t": str(r[col.get("商家确认收货时间", -1)] or r[col.get("退货创建时间", -1)] or ""),
            "amount": r[col.get("申请售后金额(元)", -1)] or 0,
            "type": r[col.get("售后类型", -1)] or "",
            "reason": r[col.get("原因", -1)] or "",
            "to_buyer": r[col.get("申请售后金额(元)", -1)] or 0,
            "to_platform": 0,
        })
    wb.close()
    return out


def parse_xhs_platform_fee(buf: bytes, source_name: str) -> list[dict]:
    """小红书平台结算: 创建时间/交易类型描述/收入/支出/账户余额/业务单号. 只算支出列."""
    wb = openpyxl.load_workbook(io.BytesIO(buf), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 2:
        return []
    header = rows[0]
    col = {h: i for i, h in enumerate(header) if h}
    out = []
    for r in rows[1:]:
        if not r[0]:
            continue
        spend = r[col.get("支出（元）", -1)]
        if not spend:
            continue
        try:
            amount = float(spend)
        except (ValueError, TypeError):
            continue
        if amount <= 0:
            continue
        out.append({
            "fee_type": r[col.get("交易类型描述", -1)] or "其他",
            "amount": amount,
            "source": source_name,
        })
    wb.close()
    return out


# ===== 拼多多 =====
def parse_pdd_orders(buf: bytes, year_month: str) -> tuple[list[dict], set]:
    """拼多多订单 27 列. 商家编码-规格维度 col 16."""
    wb = openpyxl.load_workbook(io.BytesIO(buf), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], set()
    header = rows[0]
    col = {h: i for i, h in enumerate(header) if h}
    out = []
    sku_set = set()
    for r in rows[1:]:
        if not r[0]:
            continue
        ship_t = r[col.get("发货时间", -1)]
        confirm_t = r[col.get("确认收货时间", -1)]
        deal_t = r[col.get("订单成交时间", -1)]
        # 优先按发货时间过滤,其次按成交
        t = str(ship_t or deal_t or confirm_t or "")
        if year_month not in t:
            continue
        sku = str(r[col.get("商家编码-规格维度", -1)] or "").strip().rstrip("\t").strip()
        if sku:
            sku_set.add(sku)
        # paid: 商家实收金额(已含平台补贴)
        paid_raw = r[col.get("商家实收金额(元)", -1)]
        try:
            paid = float(str(paid_raw).rstrip("\t").strip()) if paid_raw not in (None, "") else 0
        except (ValueError, TypeError):
            paid = 0
        qty_raw = r[col.get("商品数量(件)", -1)]
        try:
            qty = float(str(qty_raw).rstrip("\t").strip()) if qty_raw not in (None, "") else 0
        except (ValueError, TypeError):
            qty = 0
        price_raw = r[col.get("商品总价(元)", -1)]
        try:
            price = float(str(price_raw).rstrip("\t").strip()) if price_raw not in (None, "") else 0
        except (ValueError, TypeError):
            price = 0
        out.append({
            "main_oid": r[col.get("订单号", -1)] or "",
            "sub_oid": "",
            "create_t": str(deal_t or ""),
            "pay_t": str(ship_t or deal_t or ""),
            "sku": sku,
            "title": r[col.get("商品", -1)] or "",
            "attr": r[col.get("商品规格", -1)] or "",
            "qty": qty,
            "price": price,
            "paid": paid,
            "tracking": str(r[col.get("快递单号", -1)] or "").strip().rstrip("\t").strip(),
            "status": r[col.get("订单状态", -1)] or "",
            "refund_status": r[col.get("售后状态", -1)] or "",
        })
    wb.close()
    return out, sku_set


def parse_pdd_platform_fee(buf: bytes, source_name: str) -> list[dict]:
    """拼多多收入支出: 真表头在 r4. 字段: 商户订单号/发生时间/收入金额(+元)/支出金额(-元)/账务类型/备注/业务描述.
    只取支出列 < 0 的行作为成本."""
    wb = openpyxl.load_workbook(io.BytesIO(buf), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    # 找真表头行 (含"商户订单号")
    header_idx = None
    for i, r in enumerate(rows[:15]):
        if r and r[0] == "商户订单号":
            header_idx = i
            break
    if header_idx is None:
        wb.close()
        return []
    header = rows[header_idx]
    col = {h: i for i, h in enumerate(header) if h}
    out = []
    for r in rows[header_idx + 1:]:
        if not r or not r[0]:
            continue
        spend = r[col.get("支出金额（-元）", -1)]
        if not spend:
            continue
        try:
            amount = abs(float(spend))
        except (ValueError, TypeError):
            continue
        if amount <= 0:
            continue
        out.append({
            "fee_type": r[col.get("账务类型", -1)] or "其他",
            "amount": amount,
            "source": source_name,
        })
    wb.close()
    return out


# ===== 京东 (v0.3 完整版) =====
# 京东"货款明细" 是 30 列费用流水(同 1 订单多行=多种费用项):
# 字段: 订单编号/订单状态/订单下单时间/订单完成时间/商品编号/商品名称/商品数量/
#       扣点类型/佣金比例/费用名称/应结金额/收支方向(收入/支出)/...
# v0.3 策略: 按"订单编号"聚合 → 收入合计=销售额, 支出合计=平台费.
# 京东"商品编号"是京东ID不是商家编码 → SKU 标"未填" (待 v0.4 补 京东商品 ↔ ERP_SKU 映射)
def _strip_jd(v):
    if v is None:
        return ""
    s = str(v).strip()
    if s.startswith("'"):
        s = s[1:]
    return s.rstrip("\t").strip()


def _parse_jd_flow(buf: bytes, ext: str) -> tuple[list[tuple], list[str]]:
    """读 30 列费用流水, 返回 (rows, header). 支持 csv (utf-8-sig/gbk) 和 xlsx."""
    if ext == "csv":
        for enc in ("utf-8-sig", "gbk", "utf-8"):
            try:
                rows = list(csv.reader(io.StringIO(buf.decode(enc))))
                if rows and "订单编号" in (rows[0][0] or ""):
                    return rows[1:], rows[0]
            except Exception:
                continue
        return [], []
    else:
        wb = openpyxl.load_workbook(io.BytesIO(buf), data_only=True)
        ws = wb.active
        all_rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not all_rows:
            return [], []
        return list(all_rows[1:]), list(all_rows[0])


def parse_jd_orders(buf: bytes, year_month: str, ext: str = "xlsx") -> tuple[list[dict], set]:
    """按订单号聚合 30 列费用流水: 收入行=销售, 支出行=平台费."""
    from collections import defaultdict
    rows, header = _parse_jd_flow(buf, ext)
    if not header:
        return [], set()
    col = {h: i for i, h in enumerate(header) if h}
    if "订单编号" not in col:
        return [], set()

    by_oid: dict = defaultdict(lambda: {"income": 0.0, "spend": 0.0,
                                         "first": None, "qty": 0})
    for r in rows:
        if not r or not r[0]:
            continue
        oid = _strip_jd(r[col["订单编号"]])
        if not oid:
            continue
        amt_raw = _strip_jd(r[col.get("应结金额", -1)])
        try:
            amt = float(amt_raw)
        except (ValueError, TypeError):
            amt = 0
        direction = _strip_jd(r[col.get("收支方向", -1)])
        if direction == "收入":
            by_oid[oid]["income"] += amt
        elif direction == "支出":
            by_oid[oid]["spend"] += abs(amt)
        if by_oid[oid]["first"] is None:
            by_oid[oid]["first"] = r
            qty_raw = _strip_jd(r[col.get("商品数量", -1)])
            try:
                by_oid[oid]["qty"] = float(qty_raw) if qty_raw else 0
            except (ValueError, TypeError):
                pass

    out = []
    sku_set = set()
    for oid, d in by_oid.items():
        if d["income"] <= 0:
            continue  # 跳过纯支出订单(已退款/调账)
        first = d["first"]
        complete_t = _strip_jd(first[col.get("订单完成时间", -1)])
        order_t = _strip_jd(first[col.get("订单下单时间", -1)])
        # 归月口径: 按"下单时间"优先, 与其他平台(付款/支付/发货时间)对齐, 避免跨月单错位 (2026-05-28)
        t = order_t or complete_t
        if year_month not in t:
            continue
        sku = ""  # 京东商品编号 ≠ ERP_SKU, 留空待 v0.4 mapping
        out.append({
            "main_oid": oid,
            "sub_oid": "",
            "create_t": order_t,
            "pay_t": complete_t,
            "sku": sku,
            "title": first[col.get("商品名称", -1)] or "",
            "attr": "",
            "qty": d["qty"] or 1,
            "price": 0,
            "paid": d["income"],  # 收入合计 = 销售额
            "tracking": "",
            "status": _strip_jd(first[col.get("订单状态", -1)]),
            "refund_status": "",
        })
    return out, sku_set


def parse_jd_refunds(buf: bytes) -> list[dict]:
    """京东退款明细 47 列, 含订单号/商品编号/退款金额. 用作退款源 + 反推销售."""
    wb = openpyxl.load_workbook(io.BytesIO(buf), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 2:
        wb.close()
        return []
    # r0 是分组标题(申请信息/订单信息/...), r1 是真实表头, r2 是数据
    if rows[0] and rows[0][0] == "申请信息":
        header = rows[1]
        data_start = 2
    else:
        header = rows[0]
        data_start = 1
    col = {h: i for i, h in enumerate(header) if h}
    out = []
    for r in rows[data_start:]:
        if not r or not r[0]:
            continue
        out.append({
            "refund_id": r[col.get("服务单号", -1)] or "",
            "main_oid": str(r[col.get("订单号", -1)] or ""),
            "sku": "",  # 商品编号是京东ID不是商家编码, v0.3 补 mapping
            "title": r[col.get("商品名称", -1)] or "",
            "complete_t": str(r[col.get("商家首次处理时间", -1)] or r[col.get("审核时间", -1)] or ""),
            "amount": r[col.get("退款金额", -1)] or 0,
            "type": r[col.get("客户期望", -1)] or "",
            "reason": r[col.get("一级申请原因", -1)] or "",
            "to_buyer": r[col.get("退款金额", -1)] or 0,
            "to_platform": 0,
        })
    wb.close()
    return out


def parse_jd_platform_fee(buf: bytes, source_name: str, ext: str = "csv",
                          year_month: str = "") -> list[dict]:
    """京东平台费. 流水(30列): 按月+排除货款/提现/保证金. 汇总csv: 跳过(避免与流水重复算)."""
    if ext == "csv":
        text = None
        for enc in ("utf-8-sig", "gbk", "utf-8"):
            try:
                text = buf.decode(enc)
                break
            except Exception:
                continue
        if text is None:
            return []
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            return []
        if rows[0] and "订单编号" in (rows[0][0] or ""):
            return _jd_flow_to_fees(rows[1:], rows[0], source_name, year_month)
        # 汇总 csv 跳过 (避免与货款明细重复). 京东必须上传货款明细才能算平台费.
        return []
    else:
        wb = openpyxl.load_workbook(io.BytesIO(buf), data_only=True)
        ws = wb.active
        all_rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not all_rows:
            return []
        return _jd_flow_to_fees(list(all_rows[1:]), list(all_rows[0]), source_name, year_month)


# 京东"支出"中的非平台费类型 (资金转移/货款本体)
JD_NON_FEE_NAMES = {"货款", "提现", "保证金", "保证金充值", "保证金退还", "充值", "转账"}


def _jd_flow_to_fees(rows, header, source_name, year_month=""):
    col = {h: i for i, h in enumerate(header) if h}
    out = []
    for r in rows:
        if not r or not r[0]:
            continue
        direction = _strip_jd(r[col.get("收支方向", -1)])
        if direction != "支出":
            continue
        fee_name = _strip_jd(r[col.get("费用名称", -1)])
        if fee_name in JD_NON_FEE_NAMES:
            continue
        if year_month:
            t = _strip_jd(r[col.get("到账时间", -1)])
            if not t:
                t = _strip_jd(r[col.get("账单生成时间", -1)])
            ym_compact = year_month.replace("-", "")
            if year_month not in t and ym_compact not in t:
                continue
        amt_raw = _strip_jd(r[col.get("应结金额", -1)])
        try:
            amt = abs(float(amt_raw))
        except (ValueError, TypeError):
            continue
        if amt <= 0:
            continue
        out.append({
            "fee_type": fee_name or "其他",
            "amount": amt,
            "source": source_name,
        })
    return out


# ===== 解析分发器 (按平台 + kind 路由) =====
def detect_and_parse(filename: str, buf: bytes, year_month: str, kind_hint: str,
                     platform: str = "天猫") -> dict:
    """按 platform + kind_hint 路由. platform: 天猫/抖音/小红书/京东.
    返回 {"kind": ..., "data": [...]} 或 {"kind": "error", "msg": ...}."""
    fn = filename.lower()
    try:
        # 物流不分平台
        if kind_hint == "物流":
            if "顺丰" in filename or "sf" in fn:
                return {"kind": "物流", "data": parse_sf_logistics(buf, year_month)}
            if "中通" in filename or "zt" in fn:
                return {"kind": "物流", "data": parse_zt_logistics(buf, year_month)}
            return {"kind": "error", "msg": f"未识别快递公司: {filename}"}

        # 平台特定 parser
        if platform in ("天猫", "淘宝"):  # 淘宝结构与天猫一致, 复用同一组 parser
            if kind_hint == "订单":
                data, skus = parse_tmall_orders(buf, year_month)
                return {"kind": "订单", "data": data, "sku_set": list(skus)}
            if kind_hint == "退款":
                return {"kind": "退款", "data": parse_tmall_refunds(buf)}
            if kind_hint == "平台费":
                return {"kind": "平台费", "data": parse_tmall_platform_fee(buf, filename)}
            if kind_hint == "广告":
                return {"kind": "广告", "data": parse_tmall_ads(buf)}
        elif platform == "抖音":
            if kind_hint == "订单":
                data, skus = parse_dy_orders(buf, year_month)
                return {"kind": "订单", "data": data, "sku_set": list(skus)}
            if kind_hint == "退款":
                return {"kind": "退款", "data": parse_dy_refunds(buf)}
            if kind_hint == "平台费":
                return {"kind": "平台费", "data": parse_dy_platform_fee(buf, filename, year_month)}
            if kind_hint == "广告":
                return {"kind": "广告", "data": []}
        elif platform == "小红书":
            if kind_hint == "订单":
                data, skus = parse_xhs_orders(buf, year_month)
                return {"kind": "订单", "data": data, "sku_set": list(skus)}
            if kind_hint == "退款":
                return {"kind": "退款", "data": parse_xhs_refunds(buf)}
            if kind_hint == "平台费":
                return {"kind": "平台费", "data": parse_xhs_platform_fee(buf, filename)}
            if kind_hint == "广告":
                return {"kind": "广告", "data": []}
        elif platform == "拼多多":
            if kind_hint == "订单":
                data, skus = parse_pdd_orders(buf, year_month)
                return {"kind": "订单", "data": data, "sku_set": list(skus)}
            if kind_hint == "退款":
                return {"kind": "退款", "data": []}  # 拼多多无独立退款表
            if kind_hint == "平台费":
                return {"kind": "平台费", "data": parse_pdd_platform_fee(buf, filename)}
            if kind_hint == "广告":
                return {"kind": "广告", "data": []}
        elif platform == "京东":
            ext = "csv" if fn.endswith(".csv") else "xlsx"
            if kind_hint == "订单":
                data, skus = parse_jd_orders(buf, year_month, ext)
                return {"kind": "订单", "data": data, "sku_set": list(skus)}
            if kind_hint == "退款":
                return {"kind": "退款", "data": parse_jd_refunds(buf)}
            if kind_hint == "平台费":
                return {"kind": "平台费",
                        "data": parse_jd_platform_fee(buf, filename, ext, year_month)}
            if kind_hint == "广告":
                return {"kind": "广告", "data": []}
        return {"kind": "error", "msg": f"v0.2.7 暂不支持 {platform}/{kind_hint}"}
    except Exception as e:
        return {"kind": "error", "msg": f"解析 {filename} ({platform}/{kind_hint}): {type(e).__name__}: {e}"}
