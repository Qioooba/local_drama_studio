"""Validate design fixtures only; this does not test the application or AI models.

Usage: python -m pip install jsonschema
       python validate_design_package.py
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent
checks = []

def check(name, condition, details):
    if not condition:
        raise AssertionError(f'{name}: {details}')
    checks.append({'name': name, 'status': 'PASS', 'scope':'DESIGN_ARTIFACT_ONLY', 'details': details})

def read_json(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))

schema = read_json('contracts/explainer-run-intent.schema.json')
example = read_json('contracts/explainer-run-intent.example.json')
Draft202012Validator.check_schema(schema)
validator = Draft202012Validator(schema)
validator.validate(example)
check('json_schema_and_example', True, 'Draft 2020-12 meta-schema and positive design fixture validated; real references still require server preflight.')

mutations = [
 ('unknown_top_level_field', lambda d: d.update(actor_type='HUMAN')),
 ('cloud_fallback_forbidden', lambda d: d['inference'].update(allow_automatic_cloud_fallback=True)),
 ('local_only_cloud_provider', lambda d: d['inference'].update(configured_cloud_provider_ids=['unapproved-cloud'])),
 ('offline_external_requests', lambda d: d['research'].update(max_external_requests=1)),
 ('empty_import', lambda d: d['input'].update(source_refs=[])),
 ('none_caption_with_language', lambda d: d['outputs'][0].update(subtitle_locales=['zh-CN'])),
 ('fixed_duration_tolerance', lambda d: d['duration'].update(tolerance_percent=5)),
 ('automatic_lock_override', lambda d: d['fallback_policy'].update(preserve_human_locks=False))
]
for name, mutate in mutations:
    invalid = copy.deepcopy(example)
    mutate(invalid)
    check(name, bool(list(validator.iter_errors(invalid))), 'Invalid fixture correctly rejected by design schema; no API call or application policy test performed.')

d = read_json('sample_episode.json')
check('no_generated_media_claim', d['status']=='specification_only_no_media_generated' and d['media_artifacts']==[] and d['model_runtime_measurements'] is None, 'All media remain ungenerated, measurements unavailable, timing planned.')
segments = {s['id']:s for s in d['narration_segments']}
shots = {s['id']:s for s in d['shots']}
entities = {s['id'] for s in d['entities']}
claims = {s['id'] for s in d['claims']}
keyframes = {s['id']:s for s in d['keyframes']}
check('counts_and_unique_ids', len(segments)==24==len(d['narration_segments']) and len(shots)==40==len(d['shots']) and len(entities)==7 and len(claims)==18 and len(keyframes)==52==len(d['keyframes']), '24 segments, 40 shots, 3 characters + 2 locations + 2 props, 18 fiction facts, 52 keyframe slots.')

fps=d['format']['fps']
for kind in ('narration_segments','shots'):
    cursor=0
    for entry in d[kind]:
        start=entry['planned_start_frame'];end=entry['planned_end_frame_exclusive']
        assert start==cursor and end>start
        assert start==entry['planned_start_s']*fps and end==entry['planned_end_s']*fps
        assert end-start==entry['planned_duration_s']*fps
        assert entry['timing_status']=='planned'
        cursor=end
    check(f'{kind}_timeline', cursor==7500 and d['format']['planned_duration_s']==300, 'Contiguous half-open Chinese planned ranges [0,7500) at 25fps; not actual TTS alignment.')

for s in d['shots']:
    n=segments[s['narration_segment_id']]
    assert s['id'] in n['shot_ids']
    assert set(s['entity_ids'])<=entities and set(s['claim_ids'])<=claims
    assert n['planned_start_frame']<=s['planned_start_frame']<s['planned_end_frame_exclusive']<=n['planned_end_frame_exclusive']
    for k in s['keyframe_ids']:
        assert keyframes[k]['shot_id']==s['id']
    assert s['media_asset_id'] is None and s['generation_status']=='not_generated'
for n in segments.values():
    assert set(n['claim_ids'])<=claims
    assert all(shots[s]['narration_segment_id']==n['id'] for s in n['shot_ids'])
    assert n['audio_asset_id'] is None and n['tts_status']=='not_generated'
for k in keyframes.values():
    assert k['id'] in shots[k['shot_id']]['keyframe_ids']
    assert set(k['reference_entity_ids'])<=entities and set(k['claim_ids'])<=claims
    assert k['media_asset_id'] is None and k['generation_status']=='not_generated'
check('cross_references',True,'Segment, shot, entity, fact and keyframe references resolve; segment-shot ranges agree.')
actual_mix=dict(Counter(s['type'] for s in shots.values()))
check('shot_mix',actual_mix==d['shot_mix_planned'],actual_mix)
zh=''.join(n['narration']['zh-CN'] for n in segments.values())
en=' '.join(n['narration']['en-US'] for n in segments.values())
stats={'zh_han_characters':len(re.findall('[\u4e00-\u9fff]',zh)), 'zh_characters_with_punctuation':len(zh), 'en_whitespace_tokens':len(en.split())}
check('text_statistics',stats['zh_han_characters']==d['text_statistics']['zh_han_characters'] and stats['zh_characters_with_punctuation']==d['text_statistics']['zh_characters_with_punctuation'] and stats['en_whitespace_tokens']==d['text_statistics']['en_whitespace_tokens'],stats)
check('english_independent_clock',d['render_timeline_policy']['english_master_target_frames'] is None and d['narration_plan']['english_duration_policy']=='NATURAL_NARRATION','English timing must be measured independently; Chinese 7500 frames are not copied as actual English timing.')

matrix=(ROOT/'测试与验收矩阵.md').read_text(encoding='utf-8')
test_rows=[l for l in matrix.splitlines() if re.match(r'^\| TC-\d{3} \|',l)]
test_ids=[re.match(r'^\| (TC-\d{3})',l).group(1) for l in test_rows]
req_ids=re.findall(r'^\| (REQ-\d{2}) \|',matrix,re.M)
check('test_matrix',test_ids==[f'TC-{i:03d}' for i in range(1,91)] and all(l.endswith('| NOT_RUN |') for l in test_rows) and len(set(req_ids))==24,'90 consecutive unique product tests remain NOT_RUN; 24 unique requirements.')

html=(ROOT/'解说工厂交互线框稿.html').read_text(encoding='utf-8')
check('prototype_network_free',not re.search(r'\b(fetch\s*\(|XMLHttpRequest\b|WebSocket\s*\(|navigator\.serviceWorker)',html) and not re.search(r'(src|href)\s*=\s*[\"\x27]https?://',html,re.I),'No network API or external src/href found; static scan is scoped to supplied prototype.')
check('prototype_declares_fixture','交互线框稿' in html and '示例数据' in html,'Prototype includes visible mock-data disclosure.')

report={
 'document_kind':'DESIGN_PACKAGE_STATIC_VALIDATION',
 'executed_at_utc':datetime.now(timezone.utc).isoformat(),
 'scope':'Documentation, JSON contract, fixture references and static prototype checks only.',
 'repository_baseline':'8a63c604a1a13556dbe277d312ecf73bebb52883',
 'application_tests_status':'NOT_RUN', 'gpu_inference_status':'NOT_RUN',
 'actual_video_generated':False,'checks':checks,
 'prototype_browser_qa':read_json('原型浏览器校验结果.json'),
 'prototype_visual_qa_limit':'Browser interaction and layout checked. Screenshot environment lacked CJK glyphs; screenshots are not delivered and do not prove final Chinese typography on every OS.',
 'inputs_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(ROOT.rglob('*')) if p.is_file() and p.name not in ('文档与样例静态校验结果.json','SHA256SUMS') and p.suffix!='.zip'}
}
(ROOT/'文档与样例静态校验结果.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'artifact_checks_passed':len(checks),'product_tests':'NOT_RUN','generated_video':False,'statistics':stats},ensure_ascii=False))
