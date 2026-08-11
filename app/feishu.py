"""飞书 API 封装 — token 缓存 + bitable + sheets + drive medias + im."""
import time
import json
from urllib.parse import quote
import httpx
from typing import Any
from . import config

_TOKENS: dict[str, dict[str, Any]] = {}


async def get_token(app_id: str | None = None, app_secret: str | None = None) -> str:
    app_id = app_id or config.FEISHU_APP_ID
    app_secret = app_secret or config.FEISHU_APP_SECRET
    token = _TOKENS.get(app_id) or {"value": None, "expire": 0}
    if token["value"] and token["expire"] > time.time() + 300:
        return token["value"]
    async with httpx.AsyncClient(timeout=15) as cli:
        r = await cli.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
        )
        d = r.json()
        token["value"] = d["tenant_access_token"]
        token["expire"] = time.time() + d.get("expire", 7200)
        _TOKENS[app_id] = token
    return token["value"]


async def _req(method: str, path: str, *, use_event_app: bool = False, **kwargs) -> dict:
    if use_event_app:
        tok = await get_token(config.FEISHU_EVENT_APP_ID, config.FEISHU_EVENT_APP_SECRET)
    else:
        tok = await get_token()
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {tok}"
    async with httpx.AsyncClient(timeout=60) as cli:
        r = await cli.request(method, f"https://open.feishu.cn{path}", headers=headers, **kwargs)
        return r.json()


# ===== Bitable =====
async def bitable_get_record(app_token: str, table_id: str, record_id: str) -> dict:
    return await _req("GET", f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}")


async def bitable_search_records(app_token: str, table_id: str, filter_obj: dict | None = None,
                                 page_size: int = 100,
                                 field_names: list[str] | None = None) -> list[dict]:
    """search records (POST /search)，返回 records 数组。"""
    body: dict[str, Any] = {"automatic_fields": False}
    if filter_obj:
        body["filter"] = filter_obj
    if field_names:
        body["field_names"] = field_names
    out: list[dict] = []
    page_token = None
    while True:
        params = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        r = await _req("POST", f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
                       params=params, json=body)
        data = r.get("data") or {}
        out.extend(data.get("items") or [])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return out


async def bitable_update_record(app_token: str, table_id: str, record_id: str, fields: dict) -> dict:
    return await _req("PUT", f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
                      json={"fields": fields})


async def bitable_create_record(app_token: str, table_id: str, fields: dict) -> dict:
    return await _req("POST", f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                      json={"fields": fields})


async def bitable_batch_create(app_token: str, table_id: str, records: list[dict]) -> dict:
    """批量创建记录。records=[{"fields":{...}}, ...] (最多500条)。"""
    return await _req("POST",
                      f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                      json={"records": records})


# ===== Drive Medias (附件下载) =====
async def drive_download_media(file_token: str, extra: str | None = None) -> bytes:
    """下载多维表格附件。extra 是 bitable 的 extra 参数 (含 bitableId/tableId/recordId/fieldId)."""
    tok = await get_token()
    params = {}
    if extra:
        params["extra"] = extra
    async with httpx.AsyncClient(timeout=120) as cli:
        r = await cli.get(
            f"https://open.feishu.cn/open-apis/drive/v1/medias/{file_token}/download",
            headers={"Authorization": f"Bearer {tok}"},
            params=params,
        )
        r.raise_for_status()
        return r.content


async def drive_upload_bitable_file(file_name: str, content: bytes, parent_node: str) -> dict:
    """上传文件到 Base 附件资源池，返回 drive medias 响应。"""
    tok = await get_token()
    files = {
        "file": (file_name, content),
    }
    data = {
        "file_name": file_name,
        "parent_type": "bitable_file",
        "parent_node": parent_node,
        "size": str(len(content)),
    }
    async with httpx.AsyncClient(timeout=120) as cli:
        r = await cli.post(
            "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all",
            headers={"Authorization": f"Bearer {tok}"},
            data=data,
            files=files,
        )
        return r.json()


# ===== Sheets =====
async def sheets_create(title: str, folder_token: str | None = None) -> dict:
    body: dict[str, Any] = {"title": title}
    if folder_token:
        body["folder_token"] = folder_token
    return await _req("POST", "/open-apis/sheets/v3/spreadsheets", json=body)


async def sheets_metainfo(spreadsheet_token: str) -> dict:
    return await _req("GET", f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/metainfo")


async def sheets_batch_update(spreadsheet_token: str, requests: list) -> dict:
    return await _req("POST",
                      f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/sheets_batch_update",
                      json={"requests": requests})


async def sheets_values_put(spreadsheet_token: str, sheet_id: str, start_row: int,
                            rows: list[list]) -> dict:
    if not rows:
        return {"code": 0, "msg": "empty"}
    end_row = start_row + len(rows) - 1
    end_col = _col_letter(len(rows[0]))
    rng = f"{sheet_id}!A{start_row}:{end_col}{end_row}"
    return await _req("PUT", f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values",
                      json={"valueRange": {"range": rng, "values": rows}})


async def sheets_values_batch_update(spreadsheet_token: str, value_ranges: list) -> dict:
    return await _req("POST",
                      f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values_batch_update",
                      json={"valueRanges": value_ranges})


async def sheets_values_get(spreadsheet_token: str, range_name: str) -> dict:
    encoded_range = quote(range_name, safe="")
    return await _req("GET", f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{encoded_range}")


def _col_letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ===== Permissions =====
async def perm_add_collaborator(token: str, doc_type: str, member_id: str, perm: str = "edit",
                                member_type: str = "openid") -> dict:
    return await _req("POST",
                      f"/open-apis/drive/v1/permissions/{token}/members",
                      params={"type": doc_type, "need_notification": "false"},
                      json={"member_type": member_type, "member_id": member_id, "perm": perm})


# ===== Contact (部门/成员解析) =====
async def contact_dept_children(open_department_id: str) -> list[str]:
    """返回该部门所有子孙部门的 open_department_id (不含自己, 递归)。"""
    out: list[str] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"department_id_type": "open_department_id",
                                  "fetch_child": "true", "page_size": 50}
        if page_token:
            params["page_token"] = page_token
        r = await _req("GET",
                       f"/open-apis/contact/v3/departments/{open_department_id}/children",
                       params=params)
        data = r.get("data") or {}
        out.extend(it["open_department_id"] for it in (data.get("items") or [])
                   if it.get("open_department_id"))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return out


async def contact_dept_member_openids(open_department_id: str) -> dict[str, str]:
    """返回该部门直属成员 {open_id: name} (不含子部门)。"""
    out: dict[str, str] = {}
    page_token = None
    while True:
        params: dict[str, Any] = {"department_id": open_department_id,
                                  "department_id_type": "open_department_id", "page_size": 50}
        if page_token:
            params["page_token"] = page_token
        r = await _req("GET", "/open-apis/contact/v3/users/find_by_department", params=params)
        data = r.get("data") or {}
        for u in (data.get("items") or []):
            if u.get("open_id"):
                out[u["open_id"]] = u.get("name", "")
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return out


async def resolve_dept_member_openids(dept_roots: list[str]) -> dict[str, str]:
    """展开每个根部门(含全部子孙) → 并集成员 {open_id: name}。"""
    members: dict[str, str] = {}
    for root in dept_roots:
        nodes = [root] + await contact_dept_children(root)
        for nd in nodes:
            members.update(await contact_dept_member_openids(nd))
    return members


async def contact_dept_members_full(open_department_id: str) -> list[dict]:
    """部门直属成员 [{open_id, name, job_title}] (不含子部门)。"""
    out: list[dict] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"department_id": open_department_id,
                                  "department_id_type": "open_department_id", "page_size": 50}
        if page_token:
            params["page_token"] = page_token
        r = await _req("GET", "/open-apis/contact/v3/users/find_by_department", params=params)
        data = r.get("data") or {}
        for u in (data.get("items") or []):
            if u.get("open_id"):
                out.append({"open_id": u["open_id"], "name": u.get("name", ""),
                            "job_title": u.get("job_title", "")})
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return out


async def contact_user_get(open_id: str) -> dict:
    r = await _req("GET", f"/open-apis/contact/v3/users/{open_id}",
                   params={"user_id_type": "open_id"})
    return (r.get("data") or {}).get("user") or {}


async def contact_user_get_by_union_id(union_id: str, *, use_event_app: bool = False) -> dict:
    """按 union_id 读取用户；用于把跨 App 的同一员工映射到当前发卡 App open_id。"""
    r = await _req(
        "GET",
        f"/open-apis/contact/v3/users/{union_id}",
        params={"user_id_type": "union_id"},
        use_event_app=use_event_app,
    )
    if r.get("code") not in (None, 0):
        raise RuntimeError(f"contact union_id lookup failed: code={r.get('code')} msg={r.get('msg')}")
    return (r.get("data") or {}).get("user") or {}


async def open_id_to_union_id(open_id: str) -> str:
    if not open_id:
        return ""
    try:
        user = await contact_user_get(open_id)
        return str(user.get("union_id") or "")
    except Exception:
        return ""


async def resolve_users_by_job_title(dept_roots: list[str], job_titles: list[str]) -> dict[str, str]:
    """部门(含子)成员中 job_title ∈ job_titles 的 {open_id: name} (实时, 岗位换人自动跟随)。"""
    titles = set(job_titles)
    result: dict[str, str] = {}
    for root in dept_roots:
        nodes = [root] + await contact_dept_children(root)
        for nd in nodes:
            for u in await contact_dept_members_full(nd):
                if u.get("job_title") in titles:
                    result[u["open_id"]] = u["name"]
    return result


async def resolve_users_jt_fallback(dept_roots: list[str], job_titles: list[str]) -> dict[str, str]:
    """按职务命中; 若一个都没命中(职务名称漂移/API 无 job_title) → 该范围部门全员兜底, 防漏。"""
    r = await resolve_users_by_job_title(dept_roots, job_titles)
    if r:
        return r
    print(f"  ⚠️ 职务 {job_titles} 在 {dept_roots} 命中 0 人 → 部门全员兜底")
    return await resolve_dept_member_openids(dept_roots)


# ===== IM =====
async def chat_member_union_ids(chat_id: str, *, use_event_app: bool = True) -> set[str]:
    """返回群内员工 union_id；分页读取，供发卡前校验 @ 对象确实在目标群。"""
    out: set[str] = set()
    page_token = None
    while True:
        params: dict[str, Any] = {"member_id_type": "union_id", "page_size": 100}
        if page_token:
            params["page_token"] = page_token
        r = await _req(
            "GET",
            f"/open-apis/im/v1/chats/{chat_id}/members",
            params=params,
            use_event_app=use_event_app,
        )
        if r.get("code") not in (None, 0):
            raise RuntimeError(f"chat members lookup failed: code={r.get('code')} msg={r.get('msg')}")
        data = r.get("data") or {}
        out.update(
            str(item.get("member_id"))
            for item in (data.get("items") or [])
            if item.get("member_id")
        )
        if not data.get("has_more"):
            return out
        page_token = data.get("page_token")


async def send_text(open_id: str, text: str) -> dict:
    body = {"receive_id": open_id, "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False)}
    return await _req("POST", "/open-apis/im/v1/messages",
                      params={"receive_id_type": "open_id"}, json=body)


async def send_interactive_open_id(open_id: str, card: dict, *, use_event_app: bool = True) -> dict:
    body = {
        "receive_id": open_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    return await _req("POST", "/open-apis/im/v1/messages",
                      params={"receive_id_type": "open_id"}, json=body,
                      use_event_app=use_event_app)


async def send_interactive_chat(chat_id: str, card: dict, *, use_event_app: bool = True) -> dict:
    body = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    return await _req("POST", "/open-apis/im/v1/messages",
                      params={"receive_id_type": "chat_id"}, json=body,
                      use_event_app=use_event_app)


async def send_interactive_union_id(union_id: str, card: dict, *, use_event_app: bool = True) -> dict:
    body = {
        "receive_id": union_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    return await _req("POST", "/open-apis/im/v1/messages",
                      params={"receive_id_type": "union_id"}, json=body,
                      use_event_app=use_event_app)


async def patch_message_card(message_id: str, card: dict, *, use_event_app: bool = True) -> dict:
    body = {"content": json.dumps(card, ensure_ascii=False)}
    return await _req("PATCH", f"/open-apis/im/v1/messages/{message_id}",
                      json=body, use_event_app=use_event_app)
