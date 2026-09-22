from pathlib import Path
import importlib.util, json, sys
ROOT=Path("D:/Documents/财务与资产")
SRC=ROOT/".codex/plan/douyin-2026-08-closeout/sources"
sys.path.insert(0,str(Path.cwd()))
from app import settlement_engine, parsers
spec=importlib.util.spec_from_file_location("legacy",ROOT/"douyin_sz_aodier_2025_q3q4_trial_report.py")
legacy=importlib.util.module_from_spec(spec); spec.loader.exec_module(legacy)
costs,_=legacy.pull_cost_table()
cost_map={}
for sku,row in costs.items():
    cost_map[str(sku).strip().upper()]={
        "unit_cost": float(row.get("采购成本(财务核算)") or row.get("采购成本(ERP)") or row.get("cg_price") or 0),
        "name": row.get("ERP品名") or row.get("品名") or "",
        "source": "产品采购成本台",
    }
raw={"source_files":[],"ads":[]}
for code,shop in (("baokong","宝空店"),("fenlan","纷岚店")):
    dirs=[f"{code}-settlement",f"{code}-orders",f"{code}-july-orders"]
    for d in dirs:
        for f in (SRC/d).glob("*"):
            if f.suffix.lower() in (".csv",".xlsx",".xls") and not f.name.startswith("~$"):
                if d.endswith("settlement") and not f.name.startswith("DL"): continue
                raw["source_files"].append({"platform":"抖音","shop":shop,"fname":f.name,"buf":f.read_bytes()})
    tx=next((SRC/"account-transactions-20260918").glob(f"*{'宝空' if code=='baokong' else '纷岚'}*.csv"))
    raw["source_files"].append({"platform":"抖音","shop":shop,"fname":tx.name,"buf":tx.read_bytes()})
    ad=SRC/f"{code}-ads"/"财务流水.xlsx"
    parsed=parsers.detect_and_parse(ad.name,ad.read_bytes(),"2026-08","广告",platform="抖音")
    for row in parsed["data"]:
        row.update({"platform":"抖音","shop":shop})
        raw["ads"].append(row)
for f in (SRC/"shared-logistics").glob("*"):
    if f.suffix.lower() in (".xlsx",".xls"):
        raw["source_files"].append({"platform":"全平台","shop":"","fname":f.name,"buf":f.read_bytes()})
result=settlement_engine.compute(raw,cost_map,"2026-08")
targets={
    "抖音宝空": {
        "sales": 49570.33, "refund": 40.00, "net_sales": 49530.33,
        "platform_fee": 6162.39, "ad_fee": 22389.99, "purchase": 21965.08,
        "freight": 2528.35, "other": 465.05, "profit": -3980.53,
        "payback": 43407.94,
    },
    "抖音纷岚": {
        "sales": 2132.00, "refund": 0.00, "net_sales": 2132.00,
        "platform_fee": 227.07, "ad_fee": 563.31, "purchase": 856.80,
        "freight": 84.30, "other": 16.18, "profit": 384.34,
        "payback": 1904.93,
    },
}
summary={}
failed = False
asset_targets = {"抖音宝空": 15.00, "抖音纷岚": 0.00}
for shop,target in targets.items():
    row=next(x for x in result["monthly_rows"] if x[1:3]==["抖音",shop])
    actual = {
        "sales": row[6], "refund": row[7], "net_sales": row[8],
        "platform_fee": row[9], "ad_fee": row[10], "purchase": row[11],
        "freight": row[12], "other": row[13], "profit": row[14],
        "payback": row[16],
    }
    diffs = {key: round(actual[key] - expected, 2) for key, expected in target.items()}
    asset_net = round(sum(x[6] for x in result["asset_rows"] if x[0:2] == ["抖音", shop]), 2)
    asset_diff = round(asset_net - asset_targets[shop], 2)
    summary[shop] = {
        **actual, "margin": row[15], "p0": row[17], "target": target, "diff": diffs,
        "asset_net": asset_net, "target_asset_net": asset_targets[shop], "asset_diff": asset_diff,
    }
    failed = failed or row[17] != 0 or asset_diff != 0 or any(value != 0 for value in diffs.values())
out={
    "summary": summary,
    "douyin_assets": [x for x in result["asset_rows"] if x[0] == "抖音"],
    "douyin_p0": [x for x in result["gap_rows"] if x[0] == "P0" and x[1] == "抖音"],
}
Path("replay_202608_result.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(out,ensure_ascii=False,indent=2))
if failed or out["douyin_p0"]:
    raise SystemExit(1)
