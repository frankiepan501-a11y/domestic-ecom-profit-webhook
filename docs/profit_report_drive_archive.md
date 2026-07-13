# 毛利报表云盘归档规范

## 归档原则

财务毛利报表统一从一个总目录进入，再按业务渠道和平台归档。Base/任务台只做系统 ledger 和索引，不作为财务在云盘查找报表的主入口。

固定结构：

`飞书云盘 / 毛利报表总目录 / 渠道 / 平台文件夹 / 平台毛利表-YYYY/MM`

## 总目录

| 层级 | 云盘文件夹 | folder_token |
|---|---|---|
| 总目录 | 毛利报表总目录 | `XPWTfzDQklbVjCdI5KJc7Ta0nzc` |
| 01 | 跨境电商 | `ACRMf0jSilL7NldWkmUczewGnPg` |
| 02 | 国内电商 | `FepIf0fZslKKEudiuLQcBsT6nZg` |
| 03 | 国内线下 | `EUX3fCjpblAnqRdFhERcbTpknJf` |
| 04 | 国外B2B | `GeEMfolpOlt4Eqd4KeXccP2Fnpg` |
| 99 | 归档规则与索引 | `JNmOfGASAlwEK7dkhdIcjR1Cnzh` |

所有者：Frankie。应用保留可管理权限，用于后续自动创建和移动报表。

## 平台目录

| 渠道 | 平台/口径 | 云盘文件夹 | folder_token | 自动化落点 |
|---|---|---|---|---|
| 跨境电商 | 亚马逊 | 亚马逊毛利表 | `ENdrft9AplsKfCdjODAcWG8Knoh` | n8n `CyapOmKK0hyIJoXY` |
| 跨境电商 | 沃尔玛 | 沃尔玛毛利表 | `DxeffKg1NlbijxdnAWJceGdpnbe` | n8n `HETbzME852KlYpFl` |
| 跨境电商 | 独立站 | 独立站毛利表 | `LV1tfrY86lmW0EdVUCQcBVbInod` | n8n `2q7WSFS5G9zQpfcN` / `rkG32295bx3dVcRh` / `zaCKHu69dOLFCP1I` / `s9u91925K049t7ud` |
| 跨境电商 | TikTok Shop | TikTok毛利表 | `WnelfhIYnldeEoddfLVcAgRsnuc` | n8n `Zw17LKlAL6W9TC0V` |
| 跨境电商 | 速卖通 | 速卖通毛利表 | `SYfofVwvMlFmvCdOWpQc0P2ZnMg` | n8n `eQBUjKcBr30zgBgy` |
| 跨境电商 | 美客多 | 美客多毛利表 | `Ki6SfAXcilwqELdoUi3cgcOSnHg` | 待接入 |
| 跨境电商 | TEMU | TEMU毛利表 | `TnDTfHQGAlhMg0dYSNdcjJrxnde` | 待接入 |
| 国内电商 | 天猫/抖音/小红书/京东等 | 国内电商毛利表 | `YmLtfYSA2lLIqBdEr6kcxCYLnvy` | `domestic-ecom-profit` |

## 迁移记录

2026-07-13 已把历史报表从旧散落目录迁入新结构：

| 目标目录 | 已迁入报表数 |
|---|---:|
| 亚马逊 | 9 |
| 沃尔玛 | 10 |
| 独立站 | 42 |
| TikTok Shop | 6 |
| 速卖通 | 5 |
| 国内电商 | 1 |

旧目录仅保留兼容和追溯，不再作为财务入口，也不再作为新报表写入目标：

| 旧目录 | 旧 folder_token | 迁移后状态 |
|---|---|---|
| 亚马逊毛利表-旧目录 | `Gdu3f3QQ8ll3Vbd3JGmcqPdgnZd` | 已清空；仅作历史识别 |
| 沃尔玛毛利表-旧目录 | `CCT6fMQELl0637d5EhTcLMSMncg` | 已清空；仅作历史识别 |
| 国内电商毛利表-旧目录 | `Vr92fqp41lmUICdEMOgcl9uCnkg` | 停止作为新落点；仅作历史识别 |

## 国内电商链路

- 报表命名：`国内电商毛利表-YYYY/MM`
- 默认云盘路径：`飞书云盘 / 毛利报表总目录 / 02 国内电商 / 国内电商毛利表`
- 默认 folder token：`YmLtfYSA2lLIqBdEr6kcxCYLnvy`
- 财务确认卡必须同时展示：
  - 本平台毛利报表链接
  - 国内电商报表云盘文件夹
  - 原始资料落点说明

## 原始资料

P0 当前不把运营上传的原始资料另存为云盘文件夹。原始资料的系统落点是：

- `资料清单/附件台`
- 旧任务台对应附件字段
- 上传页的逐项附件状态

后续如果财务需要在云盘直接处理原始文件，再新增“原始资料包”归档目录，例如：

`毛利报表总目录 / 02 国内电商 / 国内电商毛利表 / 原始资料包 / YYYY-MM / 平台 / 店铺`

该项是 P1，不阻断本次月度毛利确认卡。

## 维护规则

1. 新增渠道或平台毛利报表时，先在总目录下创建对应渠道/平台文件夹，再把 folder token 写入对应服务或 n8n workflow。
2. 生成表格时必须传 `folder_token`，不要只创建在应用/个人根目录。
3. 报表卡片和通知必须给出总目录路径，不能只给 Sheet 链接。
4. 报表生成后仍必须执行权限授权：财务部、Frankie、吴晓丹、对应渠道负责人。
5. 月度毛利卡只核对当月毛利，不做平台涉税金额比对；涉税金额另走季度卡片工作流。
6. 不要再把多个平台混写到“沃尔玛”目录；独立站、TikTok、速卖通等必须写入各自平台目录。

## 权限记录

2026-07-13 已给财务部当前成员逐人添加所有目录的可阅读权限：吴晓丹、莫莉莉、林纯子。同日已给当前归档的 73 份历史 Sheet 逐人补同样的可阅读权限，避免目录可见但直点表格链接无权限。

飞书 Drive `permission.members.create` 在应用身份下不能直接给 `opendepartmentid` 部门授权，会返回 `1063001 Invalid parameter`。后续新增财务成员时，应先用通讯录展开财务部成员 open_id，再逐人授权到总目录、渠道目录和平台目录。
