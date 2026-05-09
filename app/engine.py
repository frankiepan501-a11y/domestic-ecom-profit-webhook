"""27 列毛利计算引擎 - v0.2 三维聚合 (平台, 店铺, ERP_SKU).

输入约定: orders/refunds/plat_fees/ads 每行带 platform/shop 标签
- orders: [{platform, shop, sku, qty, paid, status, tracking, main_oid, title, ...}]
- refunds: [{platform, shop, sku, amount, count, ...}]
- plat_fees: [{platform, shop, fee_type, amount}]
- ads: [{platform, shop, spend, ...}]
- logistics: [{tracking, amount, carrier, ...}] (全公司池, 无店铺)

输出: {(platform,shop,sku): {...}, "__totals_per_shop__": {...}, "__shop_log__": {...}}
"""
from collections import defaultdict


VALID_STATUS = {
    "卖家已发货，等待买家确认",
    "交易成功",
    "买家已付款",
    # 抖音/小红书/京东 状态名后续 parser 加
}


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
            sku_costs: dict[str, float], sku_names: dict[str, str]) -> dict:
    """三维聚合: key = (platform, shop, sku)."""

    # === Step 1: 销量销售额 (按 平台/店铺/SKU 三维, 仅有效订单) ===
    sku_valid: dict = defaultdict(lambda: {
        "name": "", "qty": 0, "paid": 0.0, "orders": set(), "tracking": []
    })
    status_cnt: dict = defaultdict(int)

    for o in orders:
        status_cnt[o.get("status", "")] += 1
        if o.get("status") not in VALID_STATUS:
            continue
        key = (o["platform"], o["shop"], o.get("sku") or "(未填)")
        sku_valid[key]["name"] = sku_names.get(key[2], o.get("title", ""))[:50]
        sku_valid[key]["qty"] += float(o.get("qty") or 0)
        sku_valid[key]["paid"] += float(o.get("paid") or 0)
        sku_valid[key]["orders"].add(o.get("main_oid", ""))
        if o.get("tracking"):
            sku_valid[key]["tracking"].append(
                (str(o["tracking"]).strip(), o.get("main_oid", ""), float(o.get("qty") or 0), key))

    # === Step 2: 退款按 (平台,店铺,SKU) 聚合 ===
    refund_agg: dict = defaultdict(lambda: {"refund_amt": 0.0, "refund_count": 0})
    for r in refunds:
        key = (r["platform"], r["shop"], r.get("sku") or "(未填)")
        refund_agg[key]["refund_amt"] += float(r.get("amount") or 0)
        refund_agg[key]["refund_count"] += 1

    # === Step 3: 平台费按店铺分类汇总, 然后按店铺内 SKU 销售额比例分摊 ===
    # shop_cat_total[(platform, shop)] = {L,M,N,O,P}
    shop_cat: dict = defaultdict(lambda: {"L": 0.0, "M": 0.0, "N": 0.0, "O": 0.0, "P": 0.0})
    for f in plat_fees:
        shop_key = (f["platform"], f["shop"])
        shop_cat[shop_key][classify_fee(f.get("fee_type", ""))] += float(f.get("amount") or 0)

    # 店铺销售额合计 (用于分摊)
    shop_paid: dict = defaultdict(float)
    for key, a in sku_valid.items():
        shop_paid[(key[0], key[1])] += a["paid"]

    # 分摊到 SKU
    for key, a in sku_valid.items():
        sk = (key[0], key[1])
        shop_total = shop_paid[sk] or 1
        pct = a["paid"] / shop_total
        for cat in "LMNOP":
            a[f"plat_{cat}"] = shop_cat[sk][cat] * pct

    # === Step 4: 广告费按店铺汇总, 然后店铺内 SKU 销售额比例分摊 ===
    shop_ad: dict = defaultdict(float)
    for a in ads:
        shop_ad[(a["platform"], a["shop"])] += float(a.get("spend") or 0)

    for key, sd in sku_valid.items():
        sk = (key[0], key[1])
        shop_total = shop_paid[sk] or 1
        sd["ad"] = shop_ad[sk] * (sd["paid"] / shop_total)

    # === Step 5: 物流 (全公司池, 按运单号 join) ===
    log_by_track: dict = {}
    for L in logistics:
        t = str(L.get("tracking", "")).strip()
        if t:
            log_by_track[t] = {"amt": float(L.get("amount") or 0), "carrier": L.get("carrier", "")}

    # 按 (平台,店铺) 统计物流命中
    shop_log_stat: dict = defaultdict(lambda: {"hit": 0, "miss": 0, "amt": 0.0})

    for key, sd in sku_valid.items():
        sd["log_amt"] = 0.0
        sd["log_matched"] = 0
        sd["log_unmatched"] = 0
        sk = (key[0], key[1])
        for t, oid, q, _ in sd["tracking"]:
            if t in log_by_track:
                sd["log_matched"] += 1
                sd["log_amt"] += log_by_track[t]["amt"]
                shop_log_stat[sk]["hit"] += 1
                shop_log_stat[sk]["amt"] += log_by_track[t]["amt"]
            else:
                sd["log_unmatched"] += 1
                shop_log_stat[sk]["miss"] += 1

    # === Step 6: 整理输出 + 毛利计算 ===
    result: dict = {}
    for key, a in sku_valid.items():
        platform, shop, sku = key
        refund = refund_agg.get(key, {"refund_amt": 0, "refund_count": 0})
        cost = sku_costs.get(sku, 0) * a["qty"]
        net = a["paid"] - refund["refund_amt"]
        plat_total = sum(a[f"plat_{c}"] for c in "LMNOP")
        gross = net - plat_total - a["ad"] - cost - a["log_amt"]
        result[key] = {
            "platform": platform, "shop": shop, "sku": sku,
            "name": a["name"],
            "qty": a["qty"],
            "refund_qty": refund["refund_count"],
            "paid": a["paid"],
            "refund_amt": refund["refund_amt"],
            "plat_L": a["plat_L"], "plat_M": a["plat_M"], "plat_N": a["plat_N"],
            "plat_O": a["plat_O"], "plat_P": a["plat_P"],
            "plat_total": plat_total,
            "ad": a["ad"],
            "cost": cost,
            "log_amt": a["log_amt"],
            "log_matched": a["log_matched"],
            "log_unmatched": a["log_unmatched"],
            "net_sales": net,
            "gross": gross,
            "gross_rate": gross / net if net else 0,
        }

    # 按店铺 totals
    shop_totals: dict = defaultdict(lambda: {
        "qty": 0, "refund_qty": 0, "paid": 0.0, "refund_amt": 0.0,
        "plat": 0.0, "ad": 0.0, "cost": 0.0, "log_amt": 0.0,
        "sku_count": 0, "shop_cat": shop_cat,
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

    return {
        "by_sku": result,                   # {(platform,shop,sku): {...}}
        "shop_totals": dict(shop_totals),   # {(platform,shop): {...}}
        "shop_log_stat": dict(shop_log_stat),
        "shop_cat": dict(shop_cat),         # 平台费分类按店铺
        "status_cnt": dict(status_cnt),
        "log_total_records": len(log_by_track),
    }
