"""Brief default-clock computation and cooling check; no model or app mutations."""
import json
import time
import urllib.request
import subprocess
import torch

with urllib.request.urlopen('http://127.0.0.1:8188/queue', timeout=3) as response:
    queue = json.load(response)
if queue.get('queue_running') or queue.get('queue_pending'):
    raise SystemExit('Skipped: video queue is active')

torch.backends.cuda.matmul.allow_tf32 = False
n = 8192
a = torch.ones((n, n), device='cuda', dtype=torch.bfloat16)
b = torch.ones_like(a)
c = torch.empty_like(a)
begin = time.monotonic()
while time.monotonic() - begin < 60:
    until = time.monotonic() + 4
    while time.monotonic() < until:
        for _ in range(16):
            torch.mm(a, b, out=c)
        torch.cuda.synchronize()
    if not torch.all(c == n).item():
        raise SystemExit('FAIL: incorrect GPU matrix result')
    raw = subprocess.check_output([
        'C:\\Windows\\System32\\nvidia-smi.exe',
        '--query-gpu=power.draw,power.limit,temperature.gpu,utilization.gpu,clocks.current.graphics',
        '--format=csv,noheader,nounits'], text=True).strip()
    values = [float(v.strip()) for v in raw.split(',')]
    print(json.dumps({'seconds':round(time.monotonic()-begin,1), 'watts':values[0], 'limit':values[1], 'temp':values[2], 'gpu':values[3], 'core':values[4], 'exact_result':True}), flush=True)
    if values[2] >= 78:
        raise SystemExit('Stopped: temperature target reached')
    try:
        with urllib.request.urlopen('http://127.0.0.1:8188/queue', timeout=3) as response:
            queue = json.load(response)
        if queue.get('queue_running') or queue.get('queue_pending'):
            raise SystemExit('Stopped: user video work resumed')
    except OSError:
        raise SystemExit('Stopped: workload state unavailable')
print('PASS: exact matrix result throughout brief compute/cooling check', flush=True)
