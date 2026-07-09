"""Feishu interactive cards for the domestic e-commerce profit workflow."""
from __future__ import annotations

import hashlib
import time
from typing import Any

from . import config, ledger

SCHEMA_VERSION = "domestic_profit_card_v1"


def _idem(action: str, run_id: str, card_id: str, target_id: str, nonce: str) -> str:
    raw = f"{SCHEMA_VERSION}:{action}:{run_id}:{card_id}:{target_id}:{nonce}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _payload(action: str, run_id: str, card_type: str, card_id: str, *,
             record_id: str = "", platform: str = "", period: str = "",
             gap_id: str = "", file_manifest_id: str = "", output_id: str = "",
             decision: str = "", nonce: str = "") -> dict:
    nonce = nonce or str(int(time.time() * 1000))
    target = gap_id or file_manifest_id or output_id or record_id
    return {
        "action": action,
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "card_type": card_type,
        "card_id": card_id,
        "record_id": record_id,
        "platform": platform,
        "period": period,
        "gap_id": gap_id,
        "file_manifest_id": file_manifest_id,
        "output_id": output_id,
        "decision": decision,
        "idempotency_key": _idem(action, run_id, card_id, target, nonce),
    }


def _md(text: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": text}}


def _note(text: str) -> dict:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": text}]}


def _button(text: str, payload: dict, *, button_type: str = "default") -> dict:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": button_type,
        "value": payload,
    }


def _base_card(title: str, template: str, elements: list[dict]) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }


def operation_submit_card(run_id: str, period: str, checklist: list[str]) -> dict:
    cid = ledger.card_id("ops_submit", run_id)
    nonce = str(int(time.time() * 1000))
    upload_url = f"{config.PUBLIC_BASE_URL}/upload?run_id={run_id}&token={ledger.upload_token(run_id, 'run')}"
    lines = "\n".join(f"- {item}" for item in checklist[:18]) or "- 按资料清单附件台提交本月资料"
    elements = [
        _md(
            f"**主体**：国内电商\n"
            f"**期间**：{period}\n"
            f"**平台/店铺**：天猫、抖音、小红书、拼多多、淘宝、京东\n\n"
            f"**必交资料 checklist**\n{lines}\n\n"
            f"**上传入口**：[打开资料上传页]({upload_url})"
        ),
        _md(
            "**提交前自查**\n"
            "- 订单明细必须保留 `商家编码`，能映射 ERP SKU。\n"
            "- 广告/推广没有消耗，也必须在上传页按店铺点“确认该店本月无广告消耗”。\n"
            "- 物流账单需覆盖前月、本月、次月可归属尾单。\n"
            "- 无结算不能默认跳过，必须在上传页按店铺点“确认该店本月无结算”。"
        ),
        {"tag": "hr"},
        {
            "tag": "action",
            "actions": [
                _button("已上传资料", _payload("domestic_profit_ops_files_uploaded", run_id, "ops_submit", cid,
                                           period=period, nonce=nonce), button_type="primary"),
                _button("补充说明", _payload("domestic_profit_ops_note", run_id, "ops_submit", cid,
                                      period=period, nonce=nonce)),
            ],
        },
        _note(f"run_id={run_id}；无广告/无结算改为上传页逐店铺确认，避免全局误点。"),
    ]
    return _base_card(f"🟡 [FIN·P2] 国内电商毛利报表资料提交 · {period}", "blue", elements)


def p0_gap_card(gap: dict) -> dict:
    f = gap.get("fields", {})
    run_id = ledger.extract_text(f.get("run_id"))
    gap_id = ledger.extract_text(f.get("gap_id"))
    period = ledger.extract_text(f.get("月份"))
    platform = ledger.extract_text(f.get("平台"))
    gap_type = ledger.extract_text(f.get("缺口类型"))
    evidence = ledger.extract_text(f.get("证据"))
    cid = ledger.card_id("p0_gap", run_id, gap_id)
    nonce = str(int(time.time() * 1000))
    elements = [
        _md(
            f"**gap_id**：{gap_id}\n"
            f"**平台**：{platform or '全平台'}\n"
            f"**月份**：{period or '-'}\n"
            f"**缺口原因**：{gap_type}\n"
            f"**证据/影响**：{evidence or '待补充'}\n\n"
            "请按缺口补文件或确认无数据。临时估算只能作为例外写入缺口台，不能变成长期口径。"
        ),
        {"tag": "hr"},
        {
            "tag": "action",
            "actions": [
                _button("已补充文件", _payload("domestic_profit_gap_file_added", run_id, "p0_gap", cid,
                                       gap_id=gap_id, platform=platform, period=period, nonce=nonce),
                        button_type="primary"),
                _button("确认无数据", _payload("domestic_profit_gap_no_data", run_id, "p0_gap", cid,
                                      gap_id=gap_id, platform=platform, period=period, nonce=nonce)),
                _button("接受历史临时估算", _payload("domestic_profit_gap_accept_temp", run_id, "p0_gap", cid,
                                             gap_id=gap_id, platform=platform, period=period,
                                             decision="accept_temp", nonce=nonce)),
                _button("转财务判断", _payload("domestic_profit_gap_to_finance", run_id, "p0_gap", cid,
                                       gap_id=gap_id, platform=platform, period=period,
                                       decision="to_finance", nonce=nonce)),
            ],
        },
        _note("处理后原卡会变为已处理态；重复点击不会重复写表或重复触发计算。"),
    ]
    return _base_card(f"🔴 [FIN·P0] 国内电商资料缺口 · {gap_type}", "red", elements)


def finance_confirm_card(output: dict, run: dict, gaps: list[dict]) -> dict:
    of = output.get("fields", {})
    rf = run.get("fields", {})
    run_id = ledger.extract_text(of.get("run_id")) or ledger.extract_text(rf.get("run_id"))
    output_id = ledger.extract_text(of.get("output_id"))
    period = ledger.extract_text(rf.get("期间"))
    workbook = ledger.extract_text(of.get("workbook链接"))
    tax_summary = ledger.extract_text(of.get("涉税核对摘要")) or "P0 版本：涉税核对摘要待财务复核"
    gap_lines = []
    for g in gaps[:8]:
        gf = g.get("fields", {})
        gap_lines.append(f"- {ledger.extract_text(gf.get('gap_id'))} / {ledger.extract_text(gf.get('缺口类型'))} / {ledger.extract_text(gf.get('处理结果'))}")
    if not gap_lines:
        gap_lines.append("- 无未关闭 P0 缺口")
    cid = ledger.card_id("finance_confirm", run_id, output_id)
    nonce = str(int(time.time() * 1000))
    elements = [
        _md(
            f"**期间**：{period}\n"
            f"**输出包**：{workbook or '待生成'}\n"
            f"**产品毛利 sheet gate**：月度={bool(of.get('产品毛利月度'))}，季度={bool(of.get('产品毛利季度'))}\n\n"
            f"**涉税核对摘要**\n{tax_summary}\n\n"
            f"**P0/P1 缺口与例外**\n" + "\n".join(gap_lines)
        ),
        {"tag": "hr"},
        {
            "tag": "action",
            "actions": [
                _button("确认定稿", _payload("domestic_profit_finance_approve", run_id, "finance_confirm", cid,
                                      output_id=output_id, period=period, decision="approve",
                                      nonce=nonce), button_type="primary"),
                _button("退回-资料缺口", _payload("domestic_profit_finance_return_data_gap", run_id,
                                           "finance_confirm", cid, output_id=output_id,
                                           period=period, decision="return_data_gap", nonce=nonce)),
                _button("退回-口径问题", _payload("domestic_profit_finance_return_method_gap", run_id,
                                           "finance_confirm", cid, output_id=output_id,
                                           period=period, decision="return_method_gap", nonce=nonce)),
                _button("接受临时估算", _payload("domestic_profit_finance_accept_temp", run_id,
                                           "finance_confirm", cid, output_id=output_id,
                                           period=period, decision="accept_temp", nonce=nonce)),
            ],
        },
        _note("确认会写输出报表台、报表运行台和审计日志，并 PATCH 原卡。"),
    ]
    return _base_card(f"🟡 [FIN·P2] 国内电商毛利报表财务确认 · {period}", "orange", elements)


def processed_card(title: str, message: str, *, ok: bool = True, details: dict | None = None) -> dict:
    template = "green" if ok else "grey"
    extra = ""
    if details:
        extra = "\n\n" + "\n".join(f"- {k}: {v}" for k, v in details.items() if v)
    elements = [
        _md(f"{message}{extra}\n\n此卡片已处理，无需重复点击。"),
    ]
    return _base_card(title, template, elements)
