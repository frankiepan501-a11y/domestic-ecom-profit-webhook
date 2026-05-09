"""27 列毛利计算引擎 — 复用今天 calc_powkong.py 的逻辑."""
from collections import defaultdict


VALID_STATUS = {"卖家已发货，等待买家确认", "交易成功", "买家已付款"}


def classify_fee(fee_type: str) -> str:
    """平台费类型 → L/M/N/O/P 列分类."""
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
    """核心计算 - 按 SKU 聚合 + 物流 join + 平台费分摊 + 毛利计算.
    返回 {sku: {...}, "__totals__": {...}, "__logistics__": {hit, miss, ...}}
    """
    # 1. SKU 销量销售额 (仅成交/发货)
    sku_valid = defaultdict(lambda: {"name": "", "qty": 0, "paid": 0.0,
                                     "orders": set(), "tracking": []})
    status_cnt = defaultdict(int)
    for o in orders:
        status_cnt[o["status"]] += 1
        if o["status"] not in VALID_STATUS:
            continue
        sku = o["sku"] or "(未填)"
        sku_valid[sku]["name"] = sku_names.get(sku, o["title"])[:50]
        sku_valid[sku]["qty"] += float(o["qty"] or 0)
        sku_valid[sku]["paid"] += float(o["paid"] or 0)
        sku_valid[sku]["orders"].add(o["main_oid"])
        if o["tracking"]:
            sku_valid[sku]["tracking"].append(
                (str(o["tracking"]).strip(), o["main_oid"], float(o["qty"] or 0), sku))

    total_paid = sum(a["paid"] for a in sku_valid.values())

    # 2. 退款 SKU 聚合
    refund_sku = defaultdict(lambda: {"refund_amt": 0.0, "refund_count": 0})
    for r in refunds:
        sku = r["sku"] or "(未填)"
        refund_sku[sku]["refund_amt"] += float(r["amount"] or 0)
        refund_sku[sku]["refund_count"] += 1

    # 3. 平台费按销售额分摊
    cat_total = {"L": 0.0, "M": 0.0, "N": 0.0, "O": 0.0, "P": 0.0}
    for f in plat_fees:
        cat_total[classify_fee(f["fee_type"])] += float(f["amount"] or 0)
    total_plat = sum(cat_total.values())
    for sku in sku_valid:
        pct = sku_valid[sku]["paid"] / total_paid if total_paid else 0
        sku_valid[sku]["plat_L"] = cat_total["L"] * pct
        sku_valid[sku]["plat_M"] = cat_total["M"] * pct
        sku_valid[sku]["plat_N"] = cat_total["N"] * pct
        sku_valid[sku]["plat_O"] = cat_total["O"] * pct
        sku_valid[sku]["plat_P"] = cat_total["P"] * pct

    # 4. 广告费分摊
    total_ad = sum(float(a["spend"] or 0) for a in ads)
    for sku in sku_valid:
        pct = sku_valid[sku]["paid"] / total_paid if total_paid else 0
        sku_valid[sku]["ad"] = total_ad * pct

    # 5. 物流 — 按运单号 join
    log_by_track = {}
    for L in logistics:
        t = L["tracking"]
        if t:
            log_by_track[t] = {"amt": float(L["amount"] or 0), "carrier": L["carrier"]}

    hit = 0
    miss = 0
    for sku, a in sku_valid.items():
        a["log_amt"] = 0.0
        a["log_matched"] = 0
        a["log_unmatched"] = 0
        for t, oid, q, s in a["tracking"]:
            if t in log_by_track:
                hit += 1
                a["log_matched"] += 1
                a["log_amt"] += log_by_track[t]["amt"]
            else:
                miss += 1
                a["log_unmatched"] += 1

    # 6. 整理输出
    result = {}
    for sku, a in sku_valid.items():
        refund = refund_sku.get(sku, {"refund_amt": 0, "refund_count": 0})
        cost = sku_costs.get(sku, 0) * a["qty"]
        net = a["paid"] - refund["refund_amt"]
        plat_total = a["plat_L"] + a["plat_M"] + a["plat_N"] + a["plat_O"] + a["plat_P"]
        gross = net - plat_total - a["ad"] - cost - a["log_amt"]
        result[sku] = {
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

    result["__totals__"] = {
        "total_paid": total_paid,
        "total_refund": sum(r["refund_amt"] for r in refund_sku.values()),
        "total_plat": total_plat,
        "total_ad": total_ad,
        "total_cost": sum(r["cost"] for k, r in result.items() if k != "__totals__"),
        "total_log": sum(r["log_amt"] for k, r in result.items() if k != "__totals__"),
        "total_qty": sum(r["qty"] for k, r in result.items() if k != "__totals__"),
    }
    result["__logistics__"] = {"hit": hit, "miss": miss, "total_log_records": len(log_by_track)}
    result["__status_cnt__"] = dict(status_cnt)
    result["__cat_total__"] = cat_total

    return result
