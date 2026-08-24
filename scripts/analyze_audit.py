import json
import os

with open('flash_ui_audit/master_audit_results.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print('=== TOTAL SUMMARY ===')
print('Total pages audited:', len(data['pages']))

for idx, p in enumerate(data['pages'], 1):
    print(f"\n[{idx}/{len(data['pages'])}] {p['name']} ({p['id']})")
    print(f"  URL: {p['url']}")
    print(f"  Inputs: {len(p['inputAudit'])}, Dropdowns: {len(p['selectAudit'])}, Buttons: {len(p['buttonAudit'])}, Tabs: {len(p['tabAudit'])}, Modals: {len(p['subpagesScreenshots'])}")

    # Issues
    issues = []
    if p['layoutAudit'].get('hasHorizontalOverflow'):
        issues.append("页面存在意外横向滚动条 (Horizontal Overflow)")
    if p['layoutAudit'].get('brokenImages'):
        issues.append(f"{len(p['layoutAudit']['brokenImages'])} 张破损图片/缺失缩略图 (Broken Images)")
    if p['layoutAudit'].get('clippedTexts'):
        issues.append(f"{len(p['layoutAudit']['clippedTexts'])} 处文本被意外裁剪或截断 (Clipped/Truncated Text)")
    if p['layoutAudit'].get('misalignedRows'):
        issues.append(f"{len(p['layoutAudit']['misalignedRows'])} 处按钮/表单控件未对齐 (Misaligned UI rows)")
    if p['consoleErrors']:
        issues.append(f"{len(p['consoleErrors'])} 个前端控制台错误 (Console Errors)")
    if p['networkFailures']:
        issues.append(f"{len(p['networkFailures'])} 个网络请求失败 (Network Failures)")
    
    dead_btns = [b for b in p['buttonAudit'] if b.get('status') in ['CLICK_TIMEOUT_OR_BLOCKED', 'EVAL_ERROR']]
    if dead_btns:
        issues.append(f"{len(dead_btns)} 个按钮交互超时/未响应 (Dead/Blocked Buttons)")

    if issues:
        print("  【发现问题】:")
        for iss in issues:
            print(f"    - {iss}")
        if p['consoleErrors']:
            for err in p['consoleErrors'][:3]:
                print(f"      [Console Err]: {err[:150]}")
        if p['layoutAudit'].get('misalignedRows'):
            for mis in p['layoutAudit']['misalignedRows'][:3]:
                print(f"      [Misalignment]: {mis.get('containerClass')} (diff: {mis.get('topDiff')}px) -> {[c.get('text') for c in mis.get('children', [])]}")
        if p['layoutAudit'].get('clippedTexts'):
            for clip in p['layoutAudit']['clippedTexts'][:3]:
                print(f"      [Clipped]: <{clip.get('tag')}> '{clip.get('text')}' (scroll: {clip.get('scrollWidth')}px, client: {clip.get('clientWidth')}px)")
        if dead_btns:
            for b in dead_btns[:3]:
                print(f"      [Dead Button]: '{b.get('text')}' class={b.get('className')}")
    else:
        print("  【状态】: 几何布局与交互基线正常")
