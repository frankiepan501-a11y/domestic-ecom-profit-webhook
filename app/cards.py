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


def _short(text: str, limit: int = 360) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    return text in ("true", "1", "yes", "y", "是", "已通过")


def _num(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    text = text.replace(",", "").replace("¥", "").replace("￥", "").replace("%", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _money(value: Any) -> str:
    return f"¥{_num(value):,.2f}"


def _rate(value: Any) -> str:
    if isinstance(value, str) and "%" in value:
        return value.strip()
    num = _num(value)
    if abs(num) <= 1:
        num *= 100
    return f"{num:.2f}%"


def _gap_closed_status(status: str) -> bool:
    return status in ("已关闭", "已补文件", "确认无数据", "本期暂缓，后续补充", "接受历史临时估算")


def _url_button(text: str, url: str) -> dict:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": "default",
        "url": url,
    }


def _fields(items: list[tuple[str, str]], *, short: bool = True) -> dict:
    return {
        "tag": "div",
        "fields": [
            {
                "is_short": short,
                "text": {"tag": "lark_md", "content": f"**{label}**\n{value or '-'}"},
            }
            for label, value in items
        ],
    }


def _business_gap_type(gap_type: str, evidence: str = "") -> str:
    text = f"{gap_type} {evidence}"
    if "采购" in text or "成本" in text or "SKU" in text:
        return "成本缺失/成本异常"
    if "物流" in text or "运单" in text or "快递" in text:
        return "物流费用缺失"
    if "广告" in text or "推广" in text:
        return "广告费用或无广告证明缺失"
    if "结算" in text or "到账" in text or "货款" in text:
        return "结算资料缺失"
    if "口径" in text:
        return "金额口径待确认"
    return "资料缺失或数据异常"


def _gap_line(gf: dict) -> str:
    platform = ledger.extract_text(gf.get("平台")) or "全平台"
    shop = ledger.extract_text(gf.get("店铺"))
    gap_type = ledger.extract_text(gf.get("缺口类型")) or "-"
    evidence = ledger.extract_text(gf.get("证据"))
    status = ledger.extract_text(gf.get("处理结果")) or "待处理"
    target = f"{platform}/{shop}" if shop else platform
    why = _business_gap_type(gap_type, evidence)
    suffix = f"；{_short(evidence, 70)}" if evidence else ""
    return f"- {target}：{why}（{status}）{suffix}"


def _store_line(row: dict) -> str:
    shop = row.get("shop") or row.get("店铺") or "-"
    net_sales = row.get("net_sales", row.get("净销售额(RMB)"))
    gross_profit = row.get("gross_profit", row.get("试算毛利(RMB)"))
    margin = row.get("gross_margin", row.get("试算毛利率"))
    ad_fee = row.get("ad_fee", row.get("广告费(RMB)"))
    cost = row.get("procurement_cost", row.get("采购成本(RMB)"))
    logistics = row.get("logistics_cost", row.get("尾程费用(RMB)"))
    status = row.get("status", row.get("状态")) or "已生成"
    return (
        f"- {shop}：净销售额 {_money(net_sales)}；毛利 {_money(gross_profit)}"
        f"（{_rate(margin)}）；广告 {_money(ad_fee)}；采购成本 {_money(cost)}；物流 {_money(logistics)}；{status}"
    )


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


def finance_confirm_card(output: dict, run: dict, gaps: list[dict], report_summary: dict | None = None) -> dict:
    of = output.get("fields", {})
    rf = run.get("fields", {})
    run_id = ledger.extract_text(of.get("run_id")) or ledger.extract_text(rf.get("run_id"))
    output_id = ledger.extract_text(of.get("output_id"))
    period = ledger.extract_text(rf.get("期间"))
    platform = ledger.extract_text(of.get("平台"))
    workbook = ledger.extract_text(of.get("workbook链接"))
    has_monthly = _truthy(of.get("产品毛利月度"))
    has_product = _truthy(of.get("产品毛利季度")) or has_monthly
    open_blockers: list[str] = []
    exceptions: list[str] = []
    for g in gaps:
        gf = g.get("fields", {})
        gap_platform = ledger.extract_text(gf.get("平台"))
        if platform and gap_platform not in (platform, "全平台", ""):
            continue
        p_level = ledger.extract_text(gf.get("P级"))
        status = ledger.extract_text(gf.get("处理结果")) or "待处理"
        can_finalize = bool(gf.get("是否可定稿"))
        if p_level == "P0" and not can_finalize and not _gap_closed_status(status):
            open_blockers.append(_gap_line(gf))
        elif (
            p_level == "P1"
            or status in ("本期暂缓，后续补充", "接受历史临时估算", "财务接受临时估算")
            or (can_finalize and status not in ("已补文件", "确认无数据"))
        ):
            exceptions.append(_gap_line(gf))
    report_summary = report_summary or {}
    for issue in report_summary.get("blocking_issues") or []:
        open_blockers.append(f"- {platform or '本平台'}：{issue}")
    store_rows = report_summary.get("stores") or []
    store_lines = "\n".join(_store_line(r) for r in store_rows[:8])
    if not store_lines:
        store_lines = report_summary.get("error") or "- 未读取到本平台店铺明细，请打开报表核对。"
    store_names = "、".join(str(r.get("shop") or r.get("店铺") or "-") for r in store_rows if (r.get("shop") or r.get("店铺")))
    data_issues = list(report_summary.get("issues") or [])
    if not has_monthly:
        data_issues.append("月度毛利报表未生成，不能定稿。")
    if not has_product:
        data_issues.append("产品毛利明细未生成，不能定稿。")
    gate_ok = (not open_blockers) and has_monthly and has_product
    blocking_text = "\n".join(open_blockers[:6]) if open_blockers else "- 未发现需要退回处理的资料缺失或成本缺失。"
    issue_text = "\n".join(f"- {i}" for i in data_issues[:8]) if data_issues else "- 未发现需要特别说明的金额异常；如有业务异常请按按钮退回。"
    exception_text = "\n".join(exceptions[:6]) if exceptions else "- 无临时估算或本期暂缓说明。"
    has_exceptions = bool(exceptions)
    conclusion = "资料和成本检查已通过，可确认本平台毛利报表定稿" if gate_ok else "仍有资料/成本/报表缺失，建议退回处理后再定稿"
    scope = f"{platform or '全平台'} / {period} / 结算月口径"
    cid = ledger.card_id("finance_confirm", run_id, output_id)
    nonce = str(int(time.time() * 1000))
    report_link_text = f"[打开{platform or '本期'}毛利报表]({workbook})" if workbook else "待生成"
    header_template = "green" if gate_ok else "orange"
    title_platform = f"{platform}毛利报表" if platform else "毛利报表"
    link_actions = [{"tag": "action", "actions": [_url_button(f"打开{platform or '本期'}毛利报表", workbook)]}] if workbook else []
    confirm_button = (
        _button("确认定稿（接受上述例外）", _payload("domestic_profit_finance_accept_temp", run_id,
                                             "finance_confirm", cid, output_id=output_id,
                                             platform=platform, period=period, decision="accept_temp", nonce=nonce),
                button_type="primary")
        if has_exceptions
        else _button("确认该平台定稿", _payload("domestic_profit_finance_approve", run_id, "finance_confirm", cid,
                                  output_id=output_id, platform=platform, period=period, decision="approve",
                                  nonce=nonce), button_type="primary")
    )
    confirm_text = (
        "- **确认定稿（接受上述例外）**：用于财务认可本平台毛利报表，同时接受卡片里列出的临时估算或本期暂缓说明；这个按钮本身就等于“确认定稿”，不需要再点其他确认按钮。\n"
        if has_exceptions
        else "- **确认该平台定稿**：用于财务认可本平台本月毛利报表，且没有需要接受的临时估算或本期暂缓事项；系统记录确认人和确认时间。四个平台都确认后，本月国内电商毛利报表自动归档。\n"
    )
    elements = [
        _md(f"**请财务判断**：{title_platform}是否可以定稿\n**当前结论**：{conclusion}"),
        _fields([
            ("平台", platform or "国内电商"),
            ("期间", period or "-"),
            ("统计口径", "结算月，不按下单月"),
            ("涉及店铺", store_names or "见报表"),
            ("月度毛利表", "已生成" if has_monthly else "缺失"),
            ("产品毛利明细", "已生成" if has_product else "缺失"),
        ]),
        *link_actions,
        _md(
            f"**店铺毛利摘要**\n{store_lines}\n\n"
            f"**资料/成本缺失检查**\n{blocking_text}\n\n"
            f"**财务需关注的金额异常**\n{issue_text}\n\n"
            f"**临时估算或本期暂缓说明**\n{exception_text}\n\n"
            f"**报表入口**：{report_link_text}\n"
            "- 涉税金额不在本月毛利卡核对，季度初另发“公司主体级”涉税核对卡。"
        ),
        _md(
            "**下方按钮按了以后会发生什么**\n"
            "这些按钮是一次性财务决定。确认类按钮会直接结束本平台确认；如果资料和金额口径同时有问题，请点“同时退回资料和金额问题”。\n"
            f"{confirm_text}"
            "- **退回资料缺失**：用于发现结算单、广告证明、物流账单、成本资料等原始资料不完整；系统记录为“资料缺失退回”，后续由运营补资料后重新计算/确认。\n"
            "- **退回金额/口径异常**：用于资料已齐，但金额、费用归类、退款/结算口径、负毛利原因等需要重新解释或重算；系统记录为“金额或口径异常退回”，等待修正后再发确认卡。\n"
            "- **同时退回资料和金额问题**：用于资料不完整，同时金额或口径也需要解释/重算；系统会同时记录两类问题，后续补资料并修正口径后再发确认卡。"
        ),
        {"tag": "hr"},
        {
            "tag": "action",
            "actions": [
                confirm_button,
                _button("退回资料缺失", _payload("domestic_profit_finance_return_data_gap", run_id,
                                           "finance_confirm", cid, output_id=output_id,
                                           platform=platform, period=period, decision="return_data_gap", nonce=nonce)),
                _button("退回金额/口径异常", _payload("domestic_profit_finance_return_method_gap", run_id,
                                                "finance_confirm", cid, output_id=output_id,
                                                platform=platform, period=period, decision="return_method_gap", nonce=nonce)),
                _button("同时退回资料和金额问题", _payload("domestic_profit_finance_return_data_and_method_gap", run_id,
                                                   "finance_confirm", cid, output_id=output_id,
                                                   platform=platform, period=period, decision="return_data_and_method_gap", nonce=nonce)),
            ],
        },
        _note(f"确认后系统会记录财务决定，并把原卡改成已处理态。报表批次：{run_id}。"),
    ]
    return _base_card(f"🟡 [FIN·P2] {title_platform}定稿确认 · {period}", header_template, elements)


def processed_card(title: str, message: str, *, ok: bool = True, details: dict | None = None) -> dict:
    template = "green" if ok else "grey"
    extra = ""
    if details:
        extra = "\n\n" + "\n".join(f"- {k}: {v}" for k, v in details.items() if v)
    elements = [
        _md(f"{message}{extra}\n\n此卡片已处理，无需重复点击。"),
    ]
    return _base_card(title, template, elements)
