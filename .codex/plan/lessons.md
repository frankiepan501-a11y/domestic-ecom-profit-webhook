# Lessons: 国内电商毛利缺口统一归运营

## Candidate Lessons

| Time | Symptom | Cause | Prevention | Promote to |
|---|---|---|---|---|
| 2026-08-12 | 天猫物流与抖音资料缺口没有发给运营 | 触发器只筛“采购成本/订单明细匹配”，责任分流又把部分成本派采购 | 国内毛利 4 类缺口统一进入运营分流；测试覆盖类别与收件路由 | project docs + memory candidate |
| 2026-08-12 | 同一张无回调信息卡连续两版发给 Frankie 私聊 | 把交互卡的人审测试闸机械套到纯信息卡，且没有先锁定真实业务收件人 | 纯信息卡做自动结构自检+回读后直接发业务群；只有输入/审批/回调/状态变更卡才要求人审测试 | project docs + memory candidate |

## Failed Attempts

| Attempt | What was tried | Result | Do not repeat because |
|---|---|---|---|
| 首次定向 unittest | 用模块路径加载无 `__init__.py` 的 tests | `ModuleNotFoundError` | 本仓测试使用 `unittest discover -s tests` |
| 首次整块补丁 | 同时大改 3 个文件，匹配上下文有一处不一致 | apply_patch 未生效 | 分文件、小块补丁，先查看精确行 |

## User Corrections

| Correction | Correct rule | Where to persist |
|---|---|---|
| 采购成本问题不是直接给采购 | 国内电商毛利的订单、采购成本、物流成本均由国内电商运营补充/协调 | 代码、测试、项目修复记录、memory candidate |
| 样卡不该连续发给 Frankie | 本类卡片的生产对象是国内电商运营；无审批/无回调时不以 Frankie 私聊作为默认测试闸 | 配置默认值、README、修复记录、memory candidate |

## Secret/Privacy Review

- [x] Contains no API keys, passwords, tokens, cookies, private auth blobs, or raw customer secrets.
- [x] Contains no unnecessary raw transcript.
- [x] Durable enough to help future tasks.

## 2026-08-12 新增教训

| Symptom | Cause | Prevention | Persisted to |
|---|---|---|---|
| 错误转去登录抖店 | 把“修识别规则”误解为“去平台重新取数” | 先锁定数据源和交接界面；国内毛利默认只读运营附件 | README、handoff、memory candidate |
| 首次静默重跑误判没有历史卡 | 飞书 search 返回富文本数组，代码用 `str()` 比较 action/target/result | 审计字段统一走 `ledger.extract_text()`；新增真实字段形状测试 | code + test |
| 长请求连接在 119 秒断开 | 网关连接寿命短于业务处理 | 不盲目重试；先读运行台/审计台确认是否完成 | progress + production evidence |
