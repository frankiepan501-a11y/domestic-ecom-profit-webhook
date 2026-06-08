"""领星 ERP API — 拉本地产品成本 cg_price.

签名: MD5(query_string排序) → AES-ECB(app_secret base64解码作为 key) → base64.
所有出现的 SKU 一次性翻页拉全量再筛选 — 公司 ~440 SKU 量小."""
import time
import json
import hashlib
import urllib.parse
from base64 import b64encode, b64decode
import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from . import config

_TOKEN = {"value": None, "expire": 0}

# 旧商家编码(平台后台改编码前) → 领星真实 SKU。蔡宗佑确认 2026-06-08:
# 同一产品平台后台中途改过编码, 历史订单仍带旧码 → 领星查不到成本按0算。
# 食人花/收纳包 4月用 SWSNB01 / 5月改成 SWLSNB01, 同产品都映射 FLZD04。
HIST_SKU_MAP = {
    "SWSNB01": "FLZD04",
    "SWLSNB01": "FLZD04",
    "SWSNB02": "FLZD02",
    "ZFTFL01": "FLTGO10",
    "SWYGM02": "ODRKB001",
}


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest().upper()


def _aes_sign(params: dict) -> str:
    """领星签名: AES key = APP_ID utf-8 取前16字节 \\x00 补齐. skip 空值."""
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params.keys())
                  if params[k] not in ("", None))
    md5 = _md5(qs)
    key = config.LINGXING_APP_ID.encode()[:16].ljust(16, b"\x00")
    cipher = AES.new(key, AES.MODE_ECB)
    return b64encode(cipher.encrypt(pad(md5.encode(), AES.block_size))).decode()


async def get_token() -> str:
    if _TOKEN["value"] and _TOKEN["expire"] > time.time() + 300:
        return _TOKEN["value"]
    url = "https://openapi.lingxing.com/api/auth-server/oauth/access-token"
    params = {"appId": config.LINGXING_APP_ID, "appSecret": config.LINGXING_APP_SECRET}
    async with httpx.AsyncClient(timeout=15) as cli:
        r = await cli.post(url, params=params)
        d = r.json()
    _TOKEN["value"] = d["data"]["access_token"]
    _TOKEN["expire"] = time.time() + d["data"].get("expires_in", 7200)
    return _TOKEN["value"]


async def _api(path: str, biz: dict) -> dict:
    tok = await get_token()
    ts = str(int(time.time()))
    common = {"access_token": tok, "app_key": config.LINGXING_APP_ID, "timestamp": ts}
    sp = {**common, **{k: str(v) for k, v in biz.items()}}
    sign = urllib.parse.quote(_aes_sign(sp))
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in common.items()) + "&sign=" + sign
    url = f"https://openapi.lingxing.com{path}?{qs}"
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(url, json=biz, headers={"Content-Type": "application/json"})
        return r.json()


async def get_products(skus: set[str]) -> dict[str, dict]:
    """按 SKU 列表拉本地产品 (含 cg_price 采购成本).
    返回 {sku: {name, cost, brand, category, status}}."""
    if not skus:
        return {}
    sku_set = set(skus)
    # 旧编码 → 用领星真实 SKU 查, 但结果回填到旧编码 (新码→[旧码们])
    new_to_old: dict[str, list] = {}
    for old in sku_set:
        new = HIST_SKU_MAP.get(old)
        if new:
            new_to_old.setdefault(new, []).append(old)
    query_set = sku_set | set(new_to_old)
    out: dict[str, dict] = {}
    offset = 0
    page_size = 200
    while True:
        res = await _api("/erp/sc/data/local_inventory/productList",
                         {"offset": offset, "length": page_size})
        data = res.get("data") or []
        for p in data:
            sku = p.get("sku")
            if sku in query_set:
                info = {
                    "name": p.get("product_name", ""),
                    "cost": float(p.get("cg_price") or 0),
                    "brand": p.get("brand_name", ""),
                    "category": p.get("category_name", ""),
                    "status": p.get("status_text", ""),
                }
                if sku in sku_set:
                    out[sku] = info
                for old in new_to_old.get(sku, []):  # 旧码也指向同一成本
                    out[old] = info
        total = res.get("total", 0)
        offset += page_size
        if offset >= total or not data:
            break
    return out
