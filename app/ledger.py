"""Card workflow ledger helpers.

The old task table stays as the parser/report input surface. These helpers write
only the new Base ledger tables that record card decisions, gaps, outputs, and
idempotency audit entries.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from . import config, feishu


RUN_TABLE = config.LEDGER_RUN_TABLE_ID
FILE_TABLE = config.LEDGER_FILE_TABLE_ID
GAP_TABLE = config.LEDGER_GAP_TABLE_ID
OUTPUT_TABLE = config.LEDGER_OUTPUT_TABLE_ID
AUDIT_TABLE = config.LEDGER_AUDIT_TABLE_ID


def now_ms() -> int:
    return int(time.time() * 1000)


def compact_json(value: Any, limit: int = 9000) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) > limit:
        return text[: limit - 20] + "...[truncated]"
    return text


def payload_hash(payload: Any) -> str:
    body = compact_json(payload, limit=20000)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def run_id_for_month(year_month: str) -> str:
    return f"domestic-ecom-profit-{year_month}"


def upload_token(run_id: str, file_manifest_id: str) -> str:
    raw = f"{run_id}:{file_manifest_id}:{config.WEBHOOK_BEARER_TOKEN}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def card_id(card_type: str, run_id: str, target_id: str = "") -> str:
    raw = f"{card_type}:{run_id}:{target_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("value") or value.get("link") or "")
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("name") or item.get("id") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(value)


async def records(table_id: str) -> list[dict]:
    return await feishu.bitable_search_records(config.LEDGER_APP_TOKEN, table_id)


async def find_first(table_id: str, field: str, value: str) -> dict | None:
    for rec in await records(table_id):
        if extract_text(rec.get("fields", {}).get(field)) == value:
            return rec
    return None


async def find_many(table_id: str, field: str, value: str) -> list[dict]:
    out: list[dict] = []
    for rec in await records(table_id):
        if extract_text(rec.get("fields", {}).get(field)) == value:
            out.append(rec)
    return out


async def create(table_id: str, fields: dict) -> dict:
    return await feishu.bitable_create_record(config.LEDGER_APP_TOKEN, table_id, fields)


async def update(table_id: str, record_id: str, fields: dict) -> dict:
    return await feishu.bitable_update_record(config.LEDGER_APP_TOKEN, table_id, record_id, fields)


async def ensure_run(year_month: str, legacy_summary_record_id: str | None = None) -> dict:
    rid = run_id_for_month(year_month)
    found = await find_first(RUN_TABLE, "run_id", rid)
    if found:
        fields: dict[str, Any] = {
            "最后动作": "ensure_run",
            "最后动作时间": now_ms(),
        }
        if legacy_summary_record_id and not extract_text(found.get("fields", {}).get("legacy_summary_record_id")):
            fields["legacy_summary_record_id"] = legacy_summary_record_id
        await update(RUN_TABLE, found["record_id"], fields)
        found = await find_first(RUN_TABLE, "run_id", rid)
        return found or {}
    await create(RUN_TABLE, {
        "run_id": rid,
        "主体": "国内电商",
        "期间": year_month,
        "平台范围": "天猫/抖音/小红书/拼多多/淘宝/京东",
        "当前状态": "待运营提交",
        "当前阻塞": "等待运营通过飞书卡片提交资料或确认无广告/无结算",
        "legacy_summary_record_id": legacy_summary_record_id or "",
        "最后动作": "create_run",
        "最后动作时间": now_ms(),
    })
    return await find_first(RUN_TABLE, "run_id", rid) or {}


async def update_run(run_id: str, status: str, action: str, block: str = "", **extra: Any) -> None:
    rec = await find_first(RUN_TABLE, "run_id", run_id)
    if not rec:
        return
    fields = {
        "当前状态": status,
        "当前阻塞": block,
        "最后动作": action,
        "最后动作时间": now_ms(),
    }
    fields.update(extra)
    await update(RUN_TABLE, rec["record_id"], fields)


async def append_message_id(run_id: str, message_id: str) -> None:
    if not message_id:
        return
    rec = await find_first(RUN_TABLE, "run_id", run_id)
    if not rec:
        return
    old = extract_text(rec.get("fields", {}).get("原卡message_ids"))
    parts = [p for p in old.split(",") if p] if old else []
    if message_id not in parts:
        parts.append(message_id)
    await update(RUN_TABLE, rec["record_id"], {
        "原卡message_ids": ",".join(parts),
        "最后动作时间": now_ms(),
    })


async def ensure_manifest(run_id: str, platform: str, shop: str, month: str,
                          file_type: str, required: bool) -> dict:
    fid = hashlib.sha1(f"{run_id}:{platform}:{shop}:{file_type}".encode("utf-8")).hexdigest()[:14]
    manifest_id = f"fm_{fid}"
    found = await find_first(FILE_TABLE, "file_manifest_id", manifest_id)
    if found:
        return found
    await create(FILE_TABLE, {
        "file_manifest_id": manifest_id,
        "run_id": run_id,
        "平台": platform,
        "店铺": shop,
        "月份": month,
        "文件类型": file_type,
        "必交": required,
        "状态": "待提交",
        "最后动作时间": now_ms(),
    })
    return await find_first(FILE_TABLE, "file_manifest_id", manifest_id) or {}


async def manifests_for_run(run_id: str) -> list[dict]:
    return await find_many(FILE_TABLE, "run_id", run_id)


async def mark_manifest(file_manifest_id: str, fields: dict) -> None:
    rec = await find_first(FILE_TABLE, "file_manifest_id", file_manifest_id)
    if not rec:
        return
    fields["最后动作时间"] = now_ms()
    await update(FILE_TABLE, rec["record_id"], fields)


async def create_gap(run_id: str, gap_type: str, platform: str = "", month: str = "",
                     evidence: str = "", p_level: str = "P0") -> dict:
    gid = "gap_" + hashlib.sha1(f"{run_id}:{gap_type}:{platform}:{month}:{evidence}".encode("utf-8")).hexdigest()[:14]
    found = await find_first(GAP_TABLE, "gap_id", gid)
    if found:
        return found
    await create(GAP_TABLE, {
        "gap_id": gid,
        "run_id": run_id,
        "平台": platform,
        "月份": month,
        "P级": p_level,
        "缺口类型": gap_type,
        "证据": evidence,
        "处理结果": "待处理",
        "最后动作时间": now_ms(),
    })
    return await find_first(GAP_TABLE, "gap_id", gid) or {}


async def gaps_for_run(run_id: str) -> list[dict]:
    return await find_many(GAP_TABLE, "run_id", run_id)


async def open_p0_gaps(run_id: str) -> list[dict]:
    out = []
    for rec in await gaps_for_run(run_id):
        f = rec.get("fields", {})
        p = extract_text(f.get("P级"))
        status = extract_text(f.get("处理结果"))
        can_finalize = bool(f.get("是否可定稿"))
        if p == "P0" and status not in ("已关闭", "已补文件", "确认无数据") and not can_finalize:
            out.append(rec)
    return out


async def mark_gap(gap_id: str, fields: dict) -> None:
    rec = await find_first(GAP_TABLE, "gap_id", gap_id)
    if not rec:
        return
    fields["最后动作时间"] = now_ms()
    await update(GAP_TABLE, rec["record_id"], fields)


async def create_output(run_id: str, workbook_url: str, summary: str,
                        has_monthly: bool = False, has_quarterly: bool = False) -> dict:
    output_id = "out_" + hashlib.sha1(f"{run_id}:{workbook_url}".encode("utf-8")).hexdigest()[:14]
    found = await find_first(OUTPUT_TABLE, "output_id", output_id)
    fields = {
        "run_id": run_id,
        "workbook链接": workbook_url,
        "产品毛利月度": has_monthly,
        "产品毛利季度": has_quarterly,
        "涉税核对摘要": summary,
        "财务决定": "待确认",
    }
    if found:
        await update(OUTPUT_TABLE, found["record_id"], fields)
        return await find_first(OUTPUT_TABLE, "output_id", output_id) or found
    fields["output_id"] = output_id
    await create(OUTPUT_TABLE, fields)
    return await find_first(OUTPUT_TABLE, "output_id", output_id) or {}


async def latest_output(run_id: str) -> dict | None:
    outs = await find_many(OUTPUT_TABLE, "run_id", run_id)
    return outs[-1] if outs else None


async def audit_exists(idempotency_key: str) -> bool:
    return bool(await find_first(AUDIT_TABLE, "idempotency_key", idempotency_key))


async def write_audit(idempotency_key: str, action: str, actor_open_id: str,
                      run_id: str, target_type: str, target_id: str,
                      before: Any, after: Any, payload: Any, result: str,
                      source_message_id: str = "") -> None:
    if await audit_exists(idempotency_key):
        return
    await create(AUDIT_TABLE, {
        "idempotency_key": idempotency_key,
        "action": action,
        "actor_open_id": actor_open_id,
        "target_type": target_type,
        "target_id": target_id,
        "run_id": run_id,
        "before_json": compact_json(before, limit=9000),
        "after_json": compact_json(after, limit=9000),
        "payload_hash": payload_hash(payload),
        "result": result,
        "source_message_id": source_message_id,
        "created_at": now_ms(),
    })
