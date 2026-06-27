"""毛利计算引擎 v2 (2026-06-04 锁定口径) — 三维聚合 (平台, 店铺, ERP_SKU).

口径 (赵伟俊+蔡宗佑双确认, Frankie 拍板):
- 天猫/淘宝: 订单表逐单(子单行级). 净销售 = 应付货款 − 退款金额;
  成本 = 数量 ×(1 − 退款金额/应付货款)× 进货成本. 退款金额=退款总额(含平台券).
  "无退款申请"→0. 状态=交易关闭且退款=0(未付款关) → 整单剔除(防应付虚增).
  不用退款表(避免双重剔除).
- 拼多多/抖音/小红书/京东: 订单表无退款列. 净销售/成本基准=买家实付(paid).
  退款从退款明细表按「订单号」join 到订单, 按 (1 − 退款/该单实付) 比例同步缩减净销售+成本.
  跨月退款按权责: 退款表带 pay_t 时按其归月; 不带则只要订单号在本月订单文件里就计.
- 物流: 必须含「交易关闭/全退单」的运单(运费损失). 故收集所有订单行运单, 不只有效单.
- 平台费/广告: 店铺级, 按 SKU 净销售比例分摊.

输入约定: orders/refunds/plat_fees/ads 每行带 platform/shop 标签.
"""
from collections import defaultdict

from .parsers import _ym_match


TMALL_TAOBAO = {"天猫", "淘宝"}
# 拼多多: 订单表按发货时间筛, 退款多是"仅退款未发货"/跨月退货 → 订单号 join 不上(实测0%)。
# 按会计准则(销售退回冲减退回当月收入+成本)走"退款月店铺级整扣": 本月退款表总额按店铺净销售比例分摊。
PDD = {"拼多多"}


def _money(v) -> float:
    """'无退款申请'/''/None → 0.0, 数字字符串 → float."""
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).strip().rstrip("\t").strip())
    except (TypeError, ValueError):
        return 0.0


def classify_fee(fee_type: str) -> str:
    if "佣金" in fee_type or "类目软件服务费" in fee_type:
        return "L"
    if "基础软件" in fee_type:
        return "M"
    if "营销托管" in fee_type or "天猫APP专享" in fee_type or "返点积分" in fee_type:
        return "N"
    if "消费券" in fee_type or "淘金币" in fee_type:
        return "O"
    return "P"


def compute(orders: list[dict], refunds: list[dict], plat_fees: list[dict],
            ads: list[dict], logistics: list[dict],
            sku_costs: dict[str, float], sku_names: dict[str, str],
            year_month: str = "") -> dict:
    """三维聚合: key = (platform, shop, sku)."""

    # === Pass 0a: 天猫/淘宝 按订单号预聚合 订单表退款金额 + 应付 (用于退款表补漏) ===
    tmall_oids: set = set()
    tmall_order_refund: dict = defaultdict(float)   # (plat,shop,oid) -> Σ订单表退款金额
    tmall_order_payable: dict = defaultdict(float)  # (plat,shop,oid) -> Σ应付(非交易关闭未付款)
    for o in orders:
        if o.get("platform") not in TMALL_TAOBAO:
            continue
        oid = str(o.get("main_oid") or "").strip()
        if not oid:
            continue
        k = (o["platform"], o["shop"], oid)
        tmall_oids.add(k)
        tmall_order_refund[k] += _money(o.get("order_refund"))
        if not (o.get("status") == "交易关闭" and _money(o.get("order_refund")) <= 0):
            tmall_order_payable[k] += _money(o.get("payable"))

    # 天猫/淘宝 退款表补漏 (赵伟俊口径 2026-06-04): 订单表为主 + 退款表里"本月订单且订单表没记到"的补,
    # 去重(订单表已>0的不补)、跨月(订单不在本月文件)不补(权责归他月)。
    tmall_refund_supp: dict = defaultdict(float)  # (plat,shop,oid) -> 补扣退款
    for r in refunds:
        if r.get("platform") not in TMALL_TAOBAO:
            continue
        oid = str(r.get("main_oid") or "").strip()
        if not oid:
            continue
        k = (r["platform"], r["shop"], oid)
        if k in tmall_oids and tmall_order_refund[k] <= 0:
            tmall_refund_supp[k] += _money(r.get("amount"))

    # === Pass 0b: 抖音/小红书/京东 退款明细按订单号聚合 (这3平台 join 得上) ===
    refund_by_oid: dict = defaultdict(float)
    for r in refunds:
        if r.get("platform") in TMALL_TAOBAO or r.get("platform") in PDD:
            continue  # 天猫/淘宝走订单表; 拼多多走店铺级(下面)
        if year_month and (r.get("pay_t") or "") and not _ym_match(r.get("pay_t"), year_month):
            continue  # 退款表带付款时间且跨期 → 权责归他月
        oid = str(r.get("main_oid") or "").strip()
        if not oid:
            continue
        refund_by_oid[(r["platform"], r["shop"], oid)] += _money(r.get("amount"))

    # 拼多多: 本月退款表(售后成功)总额, 按店铺级 (退款月归属, 会计准则)
    pdd_shop_refund: dict = defaultdict(float)
    for r in refunds:
        if r.get("platform") not in PDD:
            continue
        pdd_shop_refund[(r["platform"], r["shop"])] += _money(r.get("amount"))

    # 4 平台每订单实付合计 (订单号 join 用) + 拼多多店铺毛销售合计 (店铺级退款比例用)
    order_paid_total: dict = defaultdict(float)
    pdd_shop_gross: dict = defaultdict(float)
    for o in orders:
        if o.get("platform") in TMALL_TAOBAO:
            continue
        if float(o.get("paid") or 0) <= 0:
            continue
        sk = (o["platform"], o["shop"])
        if o.get("platform") in PDD:
            pdd_shop_gross[sk] += float(o.get("paid") or 0)
        else:
            oid = str(o.get("main_oid") or "").strip()
            order_paid_total[(o["platform"], o["shop"], oid)] += float(o.get("paid") or 0)

    # 拼多多店铺退款比例 (capped 1) — 每行按此比例同步缩减净销售+成本
    pdd_ratio: dict = {}
    for sk, gross in pdd_shop_gross.items():
        pdd_ratio[sk] = min(pdd_shop_refund.get(sk, 0.0) / gross, 1.0) if gross > 0 else 0.0

    matched_refund_oids: set = set()
    deposit_cnt = 0  # 定金预售单计数(透明)

    # === Pass 1: 逐单算 净销售/成本/退款, 聚合到 (平台,店铺,SKU) ===
    agg: dict = defaultdict(lambda: {
        "name": "", "qty": 0.0, "gross": 0.0, "refund_amt": 0.0,
        "refund_qty": 0, "cost": 0.0, "orders": set(), "tracking": [],
    })
    status_cnt: dict = defaultdict(int)

    for o in orders:
        plat, shop = o["platform"], o["shop"]
        sku = o.get("sku") or "(未填)"
        key = (plat, shop, sku)
        status = o.get("status", "")
        status_cnt[status] += 1
        qty = float(o.get("qty") or 0)
        main_oid = str(o.get("main_oid") or "").strip()
        tracking = str(o.get("tracking") or "").strip()
        unit_cost = sku_costs.get(sku, 0) or 0

        included = False
        is_deposit = False
        gross_line = 0.0
        refund_line = 0.0
        net_line = 0.0
        cost_line = 0.0
        qty_eff = 0.0
        refund_count = False  # 是否计入"退款数量"(天猫=订单表原值口径; 非天猫=退款表join)

        if plat in TMALL_TAOBAO:
            payable = _money(o.get("payable"))
            refund_v = _money(o.get("order_refund"))  # 退款金额 (无退款申请→0)
            # 退款数量按订单表原始退款金额>0 计数 (对齐人工筛订单表; 退款表补漏只补金额不补数量)
            refund_count = refund_v > 0
            if status == "交易关闭" and refund_v <= 0:
                # 未付款关闭(case ①): 应付>0 但未成交 → 整单剔除, 防应付虚增
                included = False
            elif payable > 0:
                # 退款表补漏: 该单订单表退款=0 但退款表有(本月) → 按应付权重分摊补扣
                supp = tmall_refund_supp.get((plat, shop, main_oid), 0.0)
                if supp > 0:
                    op = tmall_order_payable.get((plat, shop, main_oid), 0.0)
                    if op > 0:
                        refund_v += supp * (payable / op)
                refund_v = min(refund_v, payable)  # clamp 全退→=应付
                # 定金预售单/补差价凑单: 货值(数量×成本)远超收款>3倍, 或 大数量且每件实付<¥1
                # → 货未发/非真实商品 → 成本=0/销量不计虚数(净销售照算)。
                # 第2条不依赖成本, 盖住 sku 空/未映射 的补差价单(如 0.01元×5900件)。
                is_deposit = (unit_cost > 0 and qty * unit_cost > payable * 3) \
                    or (qty >= 50 and payable / qty < 1)
                gross_line = payable
                refund_line = refund_v
                net_line = payable - refund_v
                if is_deposit:
                    cost_line = 0.0
                    qty_eff = 0.0
                else:
                    cost_line = qty * (net_line / payable) * unit_cost
                    qty_eff = qty
                included = True
        else:
            paid = float(o.get("paid") or 0)
            if paid > 0:
                if plat in PDD:
                    # 拼多多: 店铺级退款比例(退款月归属, 会计准则) → 每行同比例缩减
                    refund_line = paid * pdd_ratio.get((plat, shop), 0.0)
                else:
                    ord_total = order_paid_total.get((plat, shop, main_oid), paid) or paid
                    ord_refund = refund_by_oid.get((plat, shop, main_oid), 0.0)
                    if ord_refund > 0:
                        matched_refund_oids.add((plat, shop, main_oid))
                    refund_line = min(ord_refund * (paid / ord_total), paid)  # clamp
                refund_count = refund_line > 0
                is_deposit = (unit_cost > 0 and qty * unit_cost > paid * 3) \
                    or (qty >= 50 and paid / qty < 1)
                gross_line = paid
                net_line = paid - refund_line
                if is_deposit:
                    cost_line = 0.0
                    qty_eff = 0.0
                else:
                    cost_line = qty * (net_line / paid) * unit_cost
                    qty_eff = qty
                included = True

        if is_deposit:
            deposit_cnt += 1

        a = agg[key]
        if not a["name"]:
            a["name"] = (sku_names.get(sku) or o.get("title", "") or "")[:50]
        if included:
            a["qty"] += qty_eff
            a["gross"] += gross_line
            a["refund_amt"] += refund_line
            a["cost"] += cost_line
            if refund_count:
                a["refund_qty"] += 1
            if main_oid:
                a["orders"].add(main_oid)
        # 物流: 含全退单 → 所有有运单的行都收集 (即使整单剔除/全退/定金)
        if tracking:
            a["tracking"].append((tracking, main_oid, qty, key))

    # === Pass 2: 平台费按店铺分类汇总 ===
    shop_cat: dict = defaultdict(lambda: {"L": 0.0, "M": 0.0, "N": 0.0, "O": 0.0, "P": 0.0})
    for f in plat_fees:
        shop_cat[(f["platform"], f["shop"])][classify_fee(f.get("fee_type", ""))] += float(f.get("amount") or 0)

    # 店铺净销售合计 + 毛销售合计 (分摊基准)
    shop_net: dict = defaultdict(float)
    shop_gross: dict = defaultdict(float)
    for key, a in agg.items():
        shop_net[(key[0], key[1])] += (a["gross"] - a["refund_amt"])
        shop_gross[(key[0], key[1])] += a["gross"]

    def _alloc_basis(sk, a):
        """分摊比例: 正常店按净销售; 全退店(净≤0但有毛销售)按毛销售, 防平台费/广告蒸发。"""
        if shop_net[sk] > 0:
            return (a["gross"] - a["refund_amt"]) / shop_net[sk]
        if shop_gross[sk] > 0:
            return a["gross"] / shop_gross[sk]
        return 0.0

    for key, a in agg.items():
        sk = (key[0], key[1])
        pct = _alloc_basis(sk, a)
        for cat in "LMNOP":
            a[f"plat_{cat}"] = shop_cat[sk][cat] * pct

    # === Pass 3: 广告按店铺汇总, 店铺内分摊 (同上 basis, 全退店按毛销售) ===
    shop_ad: dict = defaultdict(float)
    for a in ads:
        shop_ad[(a["platform"], a["shop"])] += float(a.get("spend") or 0)
    for key, a in agg.items():
        sk = (key[0], key[1])
        a["ad"] = shop_ad[sk] * _alloc_basis(sk, a)

    # === Pass 4: 物流按运单号 join (全公司池) ===
    log_by_track: dict = {}
    for L in logistics:
        t = str(L.get("tracking", "")).strip()
        if t:
            log_by_track[t] = {"amt": float(L.get("amount") or 0), "carrier": L.get("carrier", "")}

    shop_log_stat: dict = defaultdict(lambda: {"hit": 0, "miss": 0, "amt": 0.0})
    for key, a in agg.items():
        a["log_amt"] = 0.0
        a["log_matched"] = 0
        a["log_unmatched"] = 0
        sk = (key[0], key[1])
        for t, oid, q, _ in a["tracking"]:
            if t in log_by_track:
                a["log_matched"] += 1
                a["log_amt"] += log_by_track[t]["amt"]
                shop_log_stat[sk]["hit"] += 1
                shop_log_stat[sk]["amt"] += log_by_track[t]["amt"]
            else:
                a["log_unmatched"] += 1
                shop_log_stat[sk]["miss"] += 1

    # === Pass 5: 整理输出 + 毛利 ===
    result: dict = {}
    for key, a in agg.items():
        platform, shop, sku = key
        # 跳过纯空壳 (无成交无退款无物流) — 防止只因收集了运单但全是 miss 的脏 key
        net = a["gross"] - a["refund_amt"]
        plat_total = sum(a.get(f"plat_{c}", 0.0) for c in "LMNOP")
        gross_profit = net - plat_total - a["ad"] - a["cost"] - a["log_amt"]
        result[key] = {
            "platform": platform, "shop": shop, "sku": sku,
            "name": a["name"],
            "qty": a["qty"],
            "refund_qty": a["refund_qty"],
            "paid": a["gross"],          # 销售额 (天猫=Σ应付 / 4平台=Σ实付)
            "refund_amt": a["refund_amt"],
            "plat_L": a.get("plat_L", 0.0), "plat_M": a.get("plat_M", 0.0),
            "plat_N": a.get("plat_N", 0.0), "plat_O": a.get("plat_O", 0.0),
            "plat_P": a.get("plat_P", 0.0),
            "plat_total": plat_total,
            "ad": a["ad"],
            "cost": a["cost"],           # 已按退款比例缩减
            "log_amt": a["log_amt"],
            "log_matched": a["log_matched"],
            "log_unmatched": a["log_unmatched"],
            "net_sales": net,
            "gross": gross_profit,
            "gross_rate": gross_profit / net if net else 0,
        }

    # === 店铺 totals ===
    shop_totals: dict = defaultdict(lambda: {
        "qty": 0, "refund_qty": 0, "paid": 0.0, "refund_amt": 0.0,
        "plat": 0.0, "ad": 0.0, "cost": 0.0, "log_amt": 0.0, "sku_count": 0,
    })
    for key, d in result.items():
        sk = (key[0], key[1])
        st = shop_totals[sk]
        st["qty"] += d["qty"]
        st["refund_qty"] += d["refund_qty"]
        st["paid"] += d["paid"]
        st["refund_amt"] += d["refund_amt"]
        st["plat"] += d["plat_total"]
        st["ad"] += d["ad"]
        st["cost"] += d["cost"]
        st["log_amt"] += d["log_amt"]
        st["sku_count"] += 1

    # === orphan: 有平台费/广告/退款但无任何订单聚合的店铺 ===
    # 退款损失 (订单不在本月文件 → 该店纯退款, 按退款月计为销售退回损失, 不再静默丢弃)。
    shop_refund_orphan: dict = defaultdict(float)
    for r in refunds:
        shop_refund_orphan[(r["platform"], r["shop"])] += _money(r.get("amount"))
    orphan_keys: set = set()
    for f in plat_fees:
        orphan_keys.add((f["platform"], f["shop"]))
    for a in ads:
        orphan_keys.add((a["platform"], a["shop"]))
    for sk in shop_refund_orphan:
        orphan_keys.add(sk)
    for sk in orphan_keys:
        if sk in shop_totals:
            continue
        platform, shop = sk
        plat_sum = sum(shop_cat[sk].values())
        ad_sum = shop_ad.get(sk, 0.0)
        refund_loss = shop_refund_orphan.get(sk, 0.0)
        st = shop_totals[sk]
        st["plat"] = plat_sum
        st["ad"] = ad_sum
        st["refund_amt"] = refund_loss
        label = "(退款损失-订单不在本月)" if refund_loss > 0 else "(无成交-orphan)"
        name = ("仅退款无本月订单(销售退回损失)" if refund_loss > 0
                else "无有效订单(仅平台费/广告)")
        result[(platform, shop, label)] = {
            "platform": platform, "shop": shop, "sku": label,
            "name": name,
            "qty": 0, "refund_qty": 0, "paid": 0.0, "refund_amt": refund_loss,
            "plat_L": shop_cat[sk]["L"], "plat_M": shop_cat[sk]["M"],
            "plat_N": shop_cat[sk]["N"], "plat_O": shop_cat[sk]["O"],
            "plat_P": shop_cat[sk]["P"],
            "plat_total": plat_sum, "ad": ad_sum, "cost": 0.0,
            "log_amt": 0.0, "log_matched": 0, "log_unmatched": 0,
            "net_sales": -refund_loss,
            "gross": -plat_sum - ad_sum - refund_loss,
            "gross_rate": 0,
        }

    # 未匹配到订单的 4 平台退款 (订单不在本月文件 → 漏扣, 透明记录)
    unmatched_refund: dict = {"count": 0, "amount": 0.0}
    for k, amt in refund_by_oid.items():
        if k not in matched_refund_oids and amt > 0:
            unmatched_refund["count"] += 1
            unmatched_refund["amount"] += amt

    return {
        "by_sku": result,
        "shop_totals": dict(shop_totals),
        "shop_log_stat": dict(shop_log_stat),
        "shop_cat": dict(shop_cat),
        "status_cnt": dict(status_cnt),
        "log_total_records": len(log_by_track),
        "unmatched_refund": unmatched_refund,
        "deposit_cnt": deposit_cnt,
    }
