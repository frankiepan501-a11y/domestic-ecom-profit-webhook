# Lessons: 国内电商毛利缺口统一归运营

## Candidate Lessons

| Time | Symptom | Cause | Prevention | Promote to |
|---|---|---|---|---|
| 2026-08-12 | 天猫物流与抖音资料缺口没有发给运营 | 触发器只筛“采购成本/订单明细匹配”，责任分流又把部分成本派采购 | 国内毛利 4 类缺口统一进入运营分流；测试覆盖类别与收件路由 | project docs + memory candidate |

## Failed Attempts

| Attempt | What was tried | Result | Do not repeat because |
|---|---|---|---|
| 首次定向 unittest | 用模块路径加载无 `__init__.py` 的 tests | `ModuleNotFoundError` | 本仓测试使用 `unittest discover -s tests` |
| 首次整块补丁 | 同时大改 3 个文件，匹配上下文有一处不一致 | apply_patch 未生效 | 分文件、小块补丁，先查看精确行 |

## User Corrections

| Correction | Correct rule | Where to persist |
|---|---|---|
| 采购成本问题不是直接给采购 | 国内电商毛利的订单、采购成本、物流成本均由国内电商运营补充/协调 | 代码、测试、项目修复记录、memory candidate |

## Secret/Privacy Review

- [x] Contains no API keys, passwords, tokens, cookies, private auth blobs, or raw customer secrets.
- [x] Contains no unnecessary raw transcript.
- [x] Durable enough to help future tasks.

