"""Platform-specific views. No accounting mutations; discrepancies block new cards."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import re
from . import feishu, settlement_engine as engine

VERSION = '2026-10-10.v1'
PLATFORMS = ('抖音', '天猫')
CHECKS = ('平台范围', '实际入口', '页面覆盖', '金额核对', '日期拆分', '数量性质', '独立源表', '展示可用', '审批与旧版')
SUFFIXES = ('审核首页', '月度毛利试算', '产品毛利', '逐商品明细', '按下单月拆分',
            '费用明细', '成本明细', '物流明细', '资产明细', '源文件登记', 'A_B核对', '缺口与口径', '送审验收')


def number(value):
    return Decimal(str(value or 0))


def month(value):
    text = str(value or '').strip()
    match = re.match(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:\D|$)', text)
    if not match:
        return ''
    try:
        return datetime(*map(int, match.groups())).strftime('%Y-%m')
    except ValueError:
        return ''


def col(n):
    value = ''
    while n:
        n, r = divmod(n-1, 26); value = chr(65+r)+value
    return value


def checked(response):
    if response.get('code') != 0:
        raise RuntimeError(f"报表展示写入/回读失败：{response.get('code')} {response.get('msg')}")
    return response.get('data', {})


def build(data, period):
    """Build immutable views and explicit source/aggregate checks per platform."""
    result = {}
    for platform in PLATFORMS:
        monthly = [list(r) for r in data.get('monthly_rows', []) if r[1] == platform]
        if not monthly:
            continue
        products = [list(r) for r in data.get('product_rows', []) if r[0] == platform]
        for m in monthly:
            if not any(r[3]==m[2] for r in products) and all(not number(m[i]) for i in (5,6,7)):
                products.append([platform,'','中国',m[2],period,'非商品-当月费用','无商品结算，费用单列',0,0,0,0,m[9],m[10],m[11],m[12],m[13],m[14],m[15]])
        traces = [r for r in data.get('trace_rows', []) if r['platform'] == platform]
        issues, checks, details = [], [], []
        prefix = platform+'｜'
        split = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0)])
        for row in traces:
            order_month = month(row.get('date'))
            adjustment=row.get('quantity_note')=='非订单退款调整' and not number(row.get('qty')) and not number(row.get('sales'))
            if adjustment:order_month='非订单调整'
            if not order_month:
                issues.append(f"{row['shop']}/{row.get('sub') or row.get('order')} 缺可核的下单日期")
            if row.get('period') != period:
                issues.append(f"{row['shop']} 明细结算月份不一致")
            if row.get('quantity_note') != '商品销售数量' and not adjustment:
                issues.append(f"{row['shop']}/{row.get('order')} 数量性质待核：{row.get('quantity_note')}")
            details.append([platform,row['shop'],period,order_month or '待核',str(row.get('date') or ''),
                            row.get('order',''),row.get('sub',''),row.get('sku',''),row.get('qty',0),
                            row.get('sales',0),row.get('refund',0),row.get('source',''),row.get('quantity_note','')])
            key = (row['shop'],row.get('sku',''),order_month or '待核')
            for i, field in enumerate(('qty','sales','refund')):
                split[key][i] += number(row.get(field))

        def compare(shop, label, a, b, evidence, tolerance=Decimal('0.01')):
            delta = number(a)-number(b)
            passed = abs(delta) <= tolerance
            checks.append([platform,shop,period,label,float(number(a)),float(number(b)),float(delta),
                           '通过' if passed else '阻断',evidence])
            if not passed:
                issues.append(f'{shop} {label}差异{delta}')

        for m in monthly:
            shop = m[2]
            if m[0] != period:
                issues.append(f'{shop} 月度归月不一致')
            ps = [r for r in products if r[3] == shop and r[4] == period]
            ts = [r for r in traces if r['shop'] == shop]
            if not ps and number(m[6]):
                issues.append(f'{shop} 缺SKU页数据')
            for label, mi, pi in [('销量',5,7),('销售额',6,9),('退款',7,10),('平台费',9,11),
                                  ('广告费',10,12),('采购成本',11,13),('物流费',12,14),('其他费用',13,15),('毛利',14,16)]:
                compare(shop,'SKU/月度'+label,sum(number(r[pi]) for r in ps),m[mi],'同平台SKU与月度逐字段核对')
            for label,field,idx in [('数量','qty',5),('销售额','sales',6),('退款','refund',7)]:
                compare(shop,'月份拆分/'+label,sum(number(r.get(field)) for r in ts),m[idx],'本结算集合逐单日期明细，不是下单月全月销售')
                for sku in {r[5] for r in ps} | {r.get('sku','') for r in ts}:
                    pi={'qty':7,'sales':9,'refund':10}[field]
                    compare(shop,'逐SKU日期/'+str(sku)+'/'+label,
                            sum(number(r.get(field)) for r in ts if r.get('sku','')==sku),
                            sum(number(r[pi]) for r in ps if r[5]==sku),'逐商品与SKU汇总核对')
            compare(shop,'毛利公式',number(m[8])-sum(number(v) for v in m[9:14]),m[14],'净销售额减各项成本费用')
            compare(shop,'净销售额公式',number(m[6])-number(m[7]),m[8],'销售额减退款')
            for key,idx,mi in [('cost_rows',8,11),('log_rows',7,12)]:
                compare(shop,key,sum(number(r[idx]) for r in data.get(key,[]) if r[0]==platform and r[1]==shop),m[mi],'逐笔成本/物流明细')
            baseline = [r for r in data.get('source_baselines',[]) if r['platform']==platform and r['shop']==shop]
            if len(baseline)!=1 or not baseline[0].get('source') or not baseline[0].get('filter'):
                issues.append(f'{shop} 缺独立原件汇总及过滤条件')
            else:
                b=baseline[0]
                for label,field,idx in [('销量','qty',5),('销售额','sales',6),('退款','refund',7),
                                       ('平台费','platform_fee',9),('广告费','ad_fee',10),
                                       ('其他费用','other_fee',13),('净回款','net_receipt',16)]:
                    if field not in b:
                        issues.append(f'{shop} 缺独立原件对照：{label}')
                    else:
                        compare(shop,'官方原件/'+label,m[idx],b[field],b['source']+'；'+b['filter']+'；'+b.get('fee_proof','')+'；'+b.get('receipt_basis',''))
            if number(m[17])>0:
                issues.append(f'{shop} 仍有P0缺口')

        # No assumed physical quantities: unavailable != zero.
        product_header=list(engine.PRODUCT_HEADER)
        product_header[4]='结算月份';product_header[8]='退款折算/源表数量（非实退）'
        product_header += ['赠品实发数量','换货实发数量','实际退回数量','实物数量说明']
        product_view=[r+['待核','待核','待核','未提供仓库逐SKU实发/退回记录；不代表0，不用于抵减成本'] for r in products]
        monthly_header=list(engine.MONTHLY_HEADER)
        monthly_header[0]='结算月份';monthly_header[4]='结算记录数（非主订单数）';monthly_header[5]='商品销售数量'
        total=['',platform,platform+'合计','',*([0]*16)]
        total[0]=period
        for idx in range(4,19):
            if idx not in (15,18):total[idx]=float(sum(number(r[idx]) for r in monthly))
        total[15]=float(number(total[14])/number(total[8])) if total[8] else 0
        total[18]='见各店明细';total[19]='待财务终审'
        sheets={
            prefix+'审核首页':[['项目','内容'],['平台',platform],['结算月份',period],['币种','人民币'],['状态','待财务终审'],['格式版本',VERSION],['统计说明','按结算月确认；下单月份只拆分本结算集合。'],['检查结论','阻断，详见验收' if issues else '计算检查通过，尚需在线回读验收']],
            prefix+'月度毛利试算':[monthly_header]+monthly+[total],
            prefix+'产品毛利':[product_header]+product_view,
            prefix+'逐商品明细':[['平台','店铺','结算月份','下单月份','下单时间','主订单号','子订单号','ERP SKU','商品销售数量','销售额','退款','来源文件/行','数量性质']]+details,
            prefix+'按下单月拆分':[['平台','店铺','结算月份','下单月份','ERP SKU','商品销售数量','销售额','退款']]+[[platform,shop,period,mo,sku,*map(float,values)] for (shop,sku,mo),values in sorted(split.items())],
            prefix+'A_B核对':[['平台','店铺','结算月份','核对项','A自动计算','B对照值','差异','结论','来源/口径']]+checks,
            prefix+'缺口与口径':[['项目','说明']]+[['阻断',s] for s in dict.fromkeys(issues)]+list(data.get('note_rows',[]))+[['实物数量','赠品/换货/退回未核实的写待核，不推定0；如影响成本须补凭证。'],['费用性质','客户运费赔付、换货商品成本、承运商运费和客户付运费须按源表分别披露；不自动重分类。'],['回收价值','未核定写待核，不等于价值0；临时不抵减须有财务批准和跟踪责任人。']],
        }
        for key,name,header in [('cost_rows','成本明细',engine.COST_HEADER),('log_rows','物流明细',engine.LOG_HEADER),('fee_rows','费用明细',engine.FEE_HEADER),('asset_rows','资产明细',engine.ASSET_HEADER),('source_rows','源文件登记',engine.SOURCE_HEADER)]:
            sheets[prefix+name]=[list(header)]+[list(r) for r in data.get(key,[]) if r[0]==platform]
        sheets[prefix+'缺口与口径'] += [list(engine.GAP_HEADER)]+[list(r) for r in data.get('gap_rows',[]) if r[1]==platform]
        result[platform]={'sheets':sheets,'issues':list(dict.fromkeys(issues))}
    return result


async def publish(token, native_sheet_map, data, period):
    """Called only for newly-created workbooks, never historic candidate reports."""
    packages=build(data,period)
    root=f'https://u1wpma3xuhr.feishu.cn/sheets/{token}'
    # Keep other supported platforms accessible through explicitly shared tabs.
    mixed=any(r[1] not in PLATFORMS for r in data.get('monthly_rows',[]))
    fixed={'月度毛利试算','产品毛利_季度'}
    hide_requests=[{'updateSheet':{'properties':{'sheetId':sid,**({'title':'国内电商共用｜'+name} if mixed and name not in fixed else {'hidden':True})}}} for name,sid in native_sheet_map.items()]
    current=checked(await feishu.sheets_metainfo(token))
    existing={s['title'] for s in current['sheets']}
    requests=[]
    shared={}
    if mixed:
        shared={'国内电商共用｜月度毛利试算（按平台筛选）':[list(engine.MONTHLY_HEADER)]+data.get('monthly_rows',[]),
                '国内电商共用｜产品毛利季度（按平台筛选）':[list(engine.PRODUCT_HEADER)]+data.get('product_rows',[])}
        for name,rows in shared.items():
            if name not in existing:
                requests.append({'addSheet':{'properties':{'title':name,'gridProperties':{'rowCount':max(1000,len(rows)+1),'columnCount':26}}}})
    for platform,package in packages.items():
        for name in SUFFIXES:
            if platform+'｜'+name not in existing:
                rows=package['sheets'].get(platform+'｜'+name,[])
                requests.append({'addSheet':{'properties':{'title':platform+'｜'+name,'gridProperties':{'rowCount':max(1000,len(rows)+20),'columnCount':max(26,max((len(r) for r in rows),default=6))}}}})
    if not packages:
        return {}
    # Add visible sheets before hiding legacy sheets (cannot hide last visible tab).
    if requests or hide_requests:
        checked(await feishu.sheets_batch_update(token,requests+hide_requests))
    meta=checked(await feishu.sheets_metainfo(token))
    sm={s['title']:s['sheetId'] for s in meta['sheets']}
    for index,name in enumerate(platform+'｜'+suffix for platform in packages for suffix in SUFFIXES):
        checked(await feishu.sheets_batch_update(token,[{'updateSheet':{'properties':{'sheetId':sm[name],'index':index}}}]))
    for name,rows in shared.items():
        for start in range(0,len(rows),100):
            checked(await feishu.sheets_values_put(token,sm[name],start+1,rows[start:start+100]))
    for platform,package in packages.items():
        title=platform+'｜审核首页'
        for suffix in SUFFIXES[1:]:
            target=platform+'｜'+suffix
            package['sheets'][title].append([target,root+'?sheet='+sm[target]])
        manifest=[]
        for name,rows in package['sheets'].items():
            sid=sm[name];width=max(map(len,rows));normalized=[r+[None]*(width-len(r)) for r in rows]
            for start in range(0,len(normalized),100):
                chunk=normalized[start:start+100]
                checked(await feishu.sheets_values_put(token,sid,start+1,chunk))
            checked(await feishu.sheets_batch_update(token,[{'updateSheet':{'properties':{'sheetId':sid,'frozenRowCount':1}}}]))
            await layout(token,sid,name,width)
            styles=[{'ranges':[f'{sid}!A1:{col(width)}1'],'style':{'bold':True,'backColor':'#E8F0FE'}}]
            for c,header in enumerate(normalized[0],1):
                if any(w in str(header) for w in ('金额','费用','广告费','运费','成本','毛利','销售额','退款','回款','差异','对照值','自动计算')) and not any(w in str(header) for w in ('数量','来源','说明','单号')):
                    styles.append({'ranges':[f'{sid}!{col(c)}2:{col(c)}{max(2,len(rows))}'],'style':{'formatter':'0.00%' if '率' in str(header) else '#,##0.00'}})
                for i,row in enumerate(normalized[1:],2):
                    if isinstance(row[c-1],(int,float)) and row[c-1]<0:
                        styles.append({'ranges':[f'{sid}!{col(c)}{i}:{col(c)}{i}'],'style':{'foreColor':'#CC0000'}})
            checked(await feishu._req('PUT',f'/open-apis/sheets/v2/spreadsheets/{token}/styles_batch_update',json={'data':styles}))
            if width>2 and len(rows)>1:
                path=f'/open-apis/sheets/v3/spreadsheets/{token}/sheets/{sid}/filter'
                previous=await feishu._req('GET',path)
                method='PUT' if previous.get('code')==0 and previous.get('data',{}).get('sheet_filter_info') else 'POST'
                checked(await feishu._req(method,path,json={'range':f'{sid}!A1:{col(width)}{len(rows)}','col':'A','condition':{'filter_type':'multiValue','expected':list(dict.fromkeys(str(r[0] or '') for r in normalized[1:]))}}))
            # Full business-cell readback, not API-success-only verification.
            back=await read_rows(token,sid,len(rows),width)
            if canonical(back)!=canonical(normalized):raise RuntimeError(f'{name} 写入回读不一致')
            manifest.append([name,sid,len(rows),width,digest(back)])
        verification=[['检查项','结论','依据'],['格式版本',VERSION,'本次新生成报表']]
        evidence={
            '平台范围':'按平台分别生成；旧固定名称底表隐藏兼容，其他平台保留共用展示',
            '实际入口':'首页链接使用本次回读的目标sheet_id；发卡只替换显示入口',
            '页面覆盖':'本平台12页完整内容回读及PAGE清单',
            '金额核对':'A_B页逐字段差异、SKU/明细/月度与公式；NATIVE核审批底表',
            '日期拆分':'店铺×SKU逐项核数量/收入/退款；缺日期阻断',
            '数量性质':'虚拟支付/多SKU待核阻断；实物数量未提供明确写待核',
            '独立源表':'原件及过滤条件；缺费用/净回款独立证据阻断',
            '展示可用':'模板v1经过实际Chrome页面检查；本次格式/冻结/筛选写入及全页数值回读',
            '审批与旧版':'本入口只用于新建工作簿；NATIVE逐值核审批底表；不改历史工作簿和审批决定',
        }
        verification += [[check,'阻断' if package['issues'] else '通过',evidence[check]] for check in CHECKS]
        verification += [['PAGE',*row] for row in manifest]
        native_sid=native_sheet_map.get('月度毛利试算')
        if native_sid:
            native=await read_rows(token,native_sid,500,20)
            expected=[r for r in data['monthly_rows'] if r[1]==platform]
            actual=[r for r in native[1:] if r[1]==platform]
            if canonical(actual)!=canonical(expected):raise RuntimeError('审批底表与展示数据不一致')
            verification.append(['NATIVE',native_sid,500,20,digest(actual)])
        else:
            raise RuntimeError('缺审批底表，不能完成验收')
        # Standardized width avoids short first-row causing A:C range truncation.
        verification=[r+[None]*(6-len(r)) for r in verification]
        checked(await feishu.sheets_values_put(token,sm[platform+'｜送审验收'],1,verification))
    return packages


def canonical(rows):
    def val(x):
        if x in (None,''):return None
        if isinstance(x,list) and all(isinstance(v,dict) and v.get('type') in ('text','url') for v in x):
            return ''.join(v.get('text','') for v in x)
        if isinstance(x,(int,float,Decimal)):return format(number(x).normalize(),'f')
        return x
    return [[val(x) for x in row] for row in rows]


def digest(rows):
    return hashlib.sha256(json.dumps(canonical(rows),ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


async def read_rows(token,sid,count,width):
    rows=[]
    for start in range(1,count+1,100):
        end=min(count,start+99)
        data=checked(await feishu._req('GET',f'/open-apis/sheets/v2/spreadsheets/{token}/values/{sid}!A{start}:{col(width)}{end}',params={'valueRenderOption':'UnformattedValue'}))
        block=data.get('valueRange',{}).get('values',[])
        block += [[] for _ in range(end-start+1-len(block))]
        rows.extend([r+[None]*(width-len(r)) for r in block])
    return rows


async def layout(token,sid,name,width):
    settings=[(0,width,145)]
    if name.endswith('审核首页'):
        settings += [(0,1,220),(1,2,650)]
    for start,end,size in settings:
        checked(await feishu._req('PUT',f'/open-apis/sheets/v2/spreadsheets/{token}/dimension_range',json={
            'dimension':{'sheetId':sid,'majorDimension':'COLUMNS','startIndex':start+1,'endIndex':end},
            'dimensionProperties':{'fixedSize':size}}))


async def preflight(token,platform):
    """Recheck manifest against current values; return deep link only after success."""
    meta=checked(await feishu._req('GET',f'/open-apis/sheets/v3/spreadsheets/{token}/sheets/query'))
    sm={s['title']:s for s in meta['sheets']}
    missing=[platform+'｜'+suffix for suffix in SUFFIXES if platform+'｜'+suffix not in sm]
    if missing:return {'issues':['缺少平台展示页：'+','.join(missing)]}
    issues=[]
    for title,s in sm.items():
        if not s.get('hidden') and not title.startswith(tuple(p+'｜' for p in (*PLATFORMS,'国内电商共用'))):
            issues.append('可见页签未标平台：'+title)
    audit=await read_rows(token,sm[platform+'｜送审验收']['sheet_id'],60,6)
    checks={r[0]:r[1] for r in audit if r[0] in CHECKS}
    if not any(r[:2]==['格式版本',VERSION] for r in audit) or len(checks)!=len(CHECKS) or any(v!='通过' for v in checks.values()):
        issues.append('9项送审验收缺失或未通过')
    pages=[r for r in audit if r[0]=='PAGE']
    if {r[1] for r in pages} != {platform+'｜'+s for s in SUFFIXES if s!='送审验收'}:
        issues.append('页面验收清单不完整')
    for _,title,sid,count,width,expected in pages:
        if title not in sm or sm[title]['sheet_id']!=sid:
            issues.append('页签对应关系改变：'+str(title));continue
        if digest(await read_rows(token,sid,int(count),int(width)))!=expected:
            issues.append('验收后数据发生变化，须重新核对：'+title)
    natives=[r for r in audit if r[0]=='NATIVE']
    if len(natives)!=1:
        issues.append('缺审批底表验收')
    else:
        _,sid,count,width,expected,_=natives[0]
        native=await read_rows(token,sid,int(count),int(width))
        if digest([r for r in native[1:] if r[1]==platform])!=expected:
            issues.append('审批底表已改变，须重新核对')
    return {'issues':issues,'review_url':f'https://u1wpma3xuhr.feishu.cn/sheets/{token}?sheet='+sm[platform+'｜审核首页']['sheet_id']}
