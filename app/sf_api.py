"""顺丰丰桥 API - EXP_RECE_QUERY_SFWAYBILL 清单运费查询.

签名: base64(md5(msgData + timestamp + checkword))  (简易MD5, 不URL encode)
入参: trackingNum + trackingType=2 (真实运单号)

输出格式与 parse_sf_logistics 对齐:
  {tracking, amount, carrier="顺丰", weight, from, to, service_type, source="API"}
"""
import asyncio
import time
import json
import hashlib
import base64
import httpx
from . import config


SANDBOX_URL = "https://sfapi-sbox.sf-express.com/std/service"
PROD_URL = "https://sfapi.sf-express.com/std/service"


def _sign(msg_data_str: str, timestamp: str, checkword: str) -> str:
    raw = msg_data_str + timestamp + checkword
    return base64.b64encode(hashlib.md5(raw.encode("utf-8")).digest()).decode()


async def query_one(client: httpx.AsyncClient, tracking: str) -> dict:
    """查单笔. 返回 logistics 行 dict; 失败返回带 _error 的 dict."""
    msg_data = {"trackingNum": tracking, "trackingType": "2"}
    msg_data_str = json.dumps(msg_data, ensure_ascii=False, separators=(",", ":"))
    timestamp = str(int(time.time() * 1000))
    form = {
        "partnerID": config.SF_PARTNER_ID,
        "requestID": f"req_{timestamp}_{tracking}",
        "serviceCode": "EXP_RECE_QUERY_SFWAYBILL",
        "timestamp": timestamp,
        "msgDigest": _sign(msg_data_str, timestamp, config.SF_CHECKWORD),
        "msgData": msg_data_str,
    }
    url = PROD_URL if config.SF_ENV == "prod" else SANDBOX_URL
    try:
        r = await client.post(url, data=form, timeout=15)
        res = r.json()
    except Exception as e:
        return {"tracking": tracking, "_error": f"http: {type(e).__name__}: {e}"}

    if res.get("apiResultCode") != "A1000":
        return {"tracking": tracking, "_error": f"api: {res.get('apiResultCode')} {res.get('apiErrorMsg', '')}"}

    data = res.get("apiResultData")
    if isinstance(data, str):
        data = json.loads(data)
    if not data.get("success"):
        return {"tracking": tracking, "_error": f"data: {data.get('errorCode')} {data.get('errorMsg')}"}

    msg = data.get("msgData", {})
    info = msg.get("waybillInfo", {})
    fee_list = msg.get("waybillFeeList", []) or []
    # 运费合计 (各种 type 累加, type=1=运费, type=3=保费等)
    amount = sum(float(f.get("value") or 0) for f in fee_list)

    return {
        "carrier": "顺丰",
        "tracking": tracking,
        "amount": amount,
        "weight": float(info.get("realWeightQty") or info.get("meterageWeightQty") or 0),
        "from": f"{info.get('jProvince', '')}{info.get('jCity', '')}",
        "to": f"{info.get('dProvince', '')}{info.get('dCity', '')}",
        "service_type": info.get("limitTypeCode", ""),
        "express_type": info.get("expressTypeCode", ""),
        "monthly_account": info.get("customerAcctCode", ""),
        "source": "API",
    }


async def query_many(trackings: list[str], concurrency: int = 5) -> tuple[list[dict], list[dict]]:
    """并发批量查. 返回 (成功行, 失败行).

    concurrency: 并发数 - 顺丰 QPS 未知保守起步, 出问题往下调.
    """
    if not trackings:
        return [], []
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient() as client:
        async def _bounded(t):
            async with sem:
                return await query_one(client, t)
        results = await asyncio.gather(*[_bounded(t) for t in trackings], return_exceptions=False)

    ok, err = [], []
    for r in results:
        (err if r.get("_error") else ok).append(r)
    return ok, err
