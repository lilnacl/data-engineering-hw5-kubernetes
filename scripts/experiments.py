"""Run real Kubernetes experiments; every kubectl call is captured in evidence/."""
import argparse
import copy
import json
import math
import os
import subprocess
import time
from pathlib import Path

import yaml

from hw5.pipeline import memory_limit_mib
from hw5.storage import client, get_json

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'evidence'
GENERATED = ROOT / 'manifests' / 'runs'
GENERATED.mkdir(exist_ok=True)
KUBE = ['kubectl']


def command(args, label=None, document=None, check=True):
    result = subprocess.run(KUBE + args, input=document, text=True, capture_output=True, timeout=45)
    with (EVIDENCE / 'commands.jsonl').open('a') as f:
        f.write(json.dumps({'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                            'args': [str(a).replace(str(ROOT) + os.sep, '') for a in KUBE + args],
                            'returncode': result.returncode}) + '\n')
    if label:
        (EVIDENCE / label).write_text((result.stdout + result.stderr).replace(str(ROOT) + os.sep, ''))
    if check and result.returncode:
        raise RuntimeError(f'{args}: {result.stderr}')
    return result


def get(kind, name='', namespace='hw5'):
    args = ['get', kind] + ([name] if name else [])
    if namespace:
        args += ['-n', namespace]
    elif kind == 'pods':
        args += ['-A']
    return json.loads(command(args + ['-o', 'json']).stdout)


def save(name, value):
    (EVIDENCE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def apply(spec, filename):
    path = GENERATED / filename
    path.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True))
    command(['create', '-f', str(path)], filename + '.log')


def wait_for(fn, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(1)
    raise TimeoutError('Kubernetes condition not reached')


def pods_for(name):
    return json.loads(command(['get', 'pods', '-n', 'hw5', '-l', f'job-name={name}', '-o', 'json']).stdout)['items']


def finish_job(name, expect_success=True):
    def finished():
        job = get('job', name)
        if any(c['type'] in ('Complete', 'Failed') and c['status'] == 'True' for c in job['status'].get('conditions', [])):
            return job
    job = wait_for(finished)
    save(f'{name}-job.json', job)
    pods = pods_for(name)
    save(f'{name}-pods.json', pods)
    for pod in pods:
        pod_name = pod['metadata']['name']
        command(['logs', '-n', 'hw5', pod_name], f'{name}-{pod_name}.log', check=False)
        command(['describe', 'pod', '-n', 'hw5', pod_name], f'{name}-{pod_name}-describe.txt')
    succeeded = job['status'].get('succeeded', 0) >= job['spec'].get('completions', 1)
    assert succeeded == expect_success, (name, job['status'])
    print(f'{name}: {"Complete" if succeeded else "Failed"}', flush=True)
    return pods


def base_job(name, prefix, memory='256Mi', cpu='250m', limit_cpu='1'):
    job = yaml.safe_load((ROOT / 'manifests/job-profile.yaml').read_text())
    job['metadata']['name'] = name
    container = job['spec']['template']['spec']['containers'][0]
    container['args'] = ['--prefix', prefix]
    container['resources'] = {'requests': {'cpu': cpu, 'memory': min_memory(memory)},
                              'limits': {'cpu': limit_cpu, 'memory': memory}}
    return job


def min_memory(value):
    return '32Mi' if int(value.removesuffix('Mi')) < 64 else '64Mi'


def run_memory():
    # Same executable and source at every memory limit; no artificial allocation.
    s3 = client()
    profiles = []
    for i in range(3):
        name = f'skills-profile-{i}'
        prefix = f'runs/profile-{i}'
        apply(base_job(name, prefix), f'{name}.yaml')
        finish_job(name)
        result = get_json(s3, prefix + '/part-00.json')
        profiles.append({k: v for k, v in result.items() if k != 'counts'})
        assert result['counts'] == json.loads((ROOT / 'baseline/counts_2.json').read_text())
    peak = max(max(p['memory']['rss_peak_bytes'], p['memory'].get('memory.peak', 0)) for p in profiles)
    chosen = memory_limit_mib(peak)
    # A 64 MiB trial survived reclaim. At 48 MiB the real calculation exceeds its budget.
    oom = base_job('skills-oom', 'runs/oom', '48Mi')
    apply(oom, 'skills-oom.yaml')
    pods = finish_job('skills-oom', expect_success=False)
    terminated = pods[0]['status']['containerStatuses'][0]['state']['terminated']
    assert terminated['reason'] == 'OOMKilled' and terminated['exitCode'] == 137
    chosen_job = base_job('skills-tuned', 'runs/tuned', f'{chosen}Mi')
    apply(chosen_job, 'skills-tuned.yaml')
    finish_job('skills-tuned')
    tuned = get_json(s3, 'runs/tuned/part-00.json')
    assert tuned['counts'] == json.loads((ROOT / 'baseline/counts_2.json').read_text())
    summary = {'profiles': profiles, 'peak_bytes': peak, 'margin': 1.25,
               'round_up_mib': 16, 'chosen_limit_mib': chosen,
               'oom': terminated, 'tuned': {k: v for k, v in tuned.items() if k != 'counts'}}
    save('memory-summary.json', summary)
    return chosen


def run_parallel(chosen, reuse=False):
    job = base_job('skills-parallel', 'runs/parallel', f'{chosen}Mi')
    job['spec'].update({'completionMode': 'Indexed', 'completions': 4, 'parallelism': 4, 'backoffLimit': 4})
    job['spec']['template']['spec']['containers'][0]['args'] += ['--key', 'parts/part-{index:02d}.csv']
    if not reuse:
        apply(job, 'skills-parallel.yaml')
    # Capture overlapping Running pods before the fast workers can finish.
    snapshots = []
    for _ in range(0 if reuse else 30):
        pods = pods_for('skills-parallel')
        snapshots.append({'time': time.time(), 'pods': [{'name': p['metadata']['name'],
                          'phase': p['status']['phase'], 'index': p['metadata']['annotations'].get('batch.kubernetes.io/job-completion-index')}
                         for p in pods]})
        if sum(p['status']['phase'] == 'Running' for p in pods) == 4:
            break
        time.sleep(0.25)
    if not reuse:
        save('parallel-overlap.json', snapshots)
    finish_job('skills-parallel')
    # Pretty-printed baseline exceeds the 1 MiB ConfigMap limit; compact JSON preserves every count.
    compact = ROOT / 'work' / 'baseline-compact.json'
    compact.parent.mkdir(exist_ok=True)
    compact.write_text(json.dumps(json.loads((ROOT / 'baseline/counts_2.json').read_text()), ensure_ascii=False, separators=(',', ':')))
    command(['create', 'configmap', 'hw3-baseline', '-n', 'hw5', f'--from-file=counts_2.json={compact}'], 'baseline-configmap.log')
    reducer = base_job('skills-reducer', 'runs/parallel', f'{chosen}Mi')
    spec = reducer['spec']['template']['spec']
    spec['volumes'] = [{'name': 'baseline', 'configMap': {'name': 'hw3-baseline'}}]
    spec['containers'][0]['volumeMounts'] = [{'name': 'baseline', 'mountPath': '/baseline', 'readOnly': True}]
    spec['containers'][0]['args'] += ['--mode', 'reduce', '--parts', '4']
    apply(reducer, 'skills-reducer.yaml')
    finish_job('skills-reducer')
    s3 = client()
    merged = get_json(s3, 'runs/parallel/merged.json')
    assert merged['baseline_equal'] and merged['rows'] == 229388 and merged['selected_rows'] == 3123
    (ROOT / 'results/counts_2.json').write_text(json.dumps(merged['counts'], ensure_ascii=False, indent=2) + '\n')
    (ROOT / 'results/top10.json').write_text(json.dumps(merged['top10'], ensure_ascii=False, indent=2) + '\n')
    save('parallel-summary.json', {k: v for k, v in merged.items() if k != 'counts'})
    shards = []
    for i in range(4):
        part = get_json(s3, f'runs/parallel/part-{i:02d}.json')
        shards.append({k: v for k, v in part.items() if k != 'counts'})
    save('parallel-shards.json', shards)


def cpu_milli(value):
    return int(value[:-1]) if value.endswith('m') else int(float(value) * 1000)


def free_cpu():
    node = get('nodes', namespace=None)['items'][0]
    capacity = cpu_milli(node['status']['allocatable']['cpu'])
    pods = get('pods', namespace=None)['items']
    active = [p for p in pods if p['status']['phase'] not in ('Succeeded', 'Failed') and p['spec'].get('nodeName')]
    used = sum(cpu_milli(c.get('resources', {}).get('requests', {}).get('cpu', '0')) for p in active for c in p['spec']['containers'])
    return capacity - used, {'allocatable_millicpu': capacity, 'existing_requests_millicpu': used}


def task_pod(name, cpu, namespace='hw5'):
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': namespace},
            'spec': {'restartPolicy': 'Never', 'terminationGracePeriodSeconds': 1,
                     'automountServiceAccountToken': False,
                     'containers': [{'name': 'reservation', 'image': 'de-hw5-skills:1', 'imagePullPolicy': 'Never',
                                     'command': ['python', '-c', 'import time; print("CPU request reserved", flush=True); time.sleep(600)'],
                                     'resources': {'requests': {'cpu': cpu, 'memory': '64Mi'},
                                                   'limits': {'cpu': cpu, 'memory': '128Mi'}}}]}}


def run_scheduling(chosen):
    command(['apply', '-f', 'manifests/priorities.yaml'], 'priorities-apply.log')
    available, cpu = free_cpu()
    blocker_cpu = f'{available - 1000}m'
    blocker = task_pod('cpu-reservation', blocker_cpu)
    blocker['spec']['priorityClassName'] = 'hw5-high'
    apply(blocker, 'cpu-reservation.yaml')
    wait_for(lambda: get('pod', 'cpu-reservation')['status']['phase'] == 'Running')
    waiting = base_job('skills-cpu-wait', 'runs/cpu-wait', f'{chosen}Mi', '2000m', '2')
    apply(waiting, 'skills-cpu-wait.yaml')
    def is_pending():
        pods = pods_for('skills-cpu-wait')
        if pods and any(c['type'] == 'PodScheduled' and c['status'] == 'False' and 'Insufficient cpu' in c.get('message', '') for c in pods[0]['status'].get('conditions', [])):
            return pods[0]
    pending = wait_for(is_pending)
    save('cpu-pending.json', pending)
    save('cpu-budget.json', {**cpu, 'blocker_request': blocker_cpu, 'waiting_request': '2000m'})
    command(['describe', 'pod', '-n', 'hw5', pending['metadata']['name']], 'cpu-pending-describe.txt')
    command(['delete', 'pod', 'cpu-reservation', '-n', 'hw5', '--wait=true'], 'cpu-release.log')
    finish_job('skills-cpu-wait')

    available, cpu = free_cpu()
    low = base_job('skills-low', 'runs/low', f'{chosen}Mi', f'{available - 1000}m', f'{available - 1000}m')
    low['spec']['activeDeadlineSeconds'] = 600
    low['spec']['template']['spec']['priorityClassName'] = 'hw5-low'
    low['spec']['template']['spec']['terminationGracePeriodSeconds'] = 15
    c = low['spec']['template']['spec']['containers'][0]
    # Keep actual HW3 calculation running, and preserve the preemption condition during cleanup.
    c['command'] = ['python', '-c']
    c['args'] = ['import signal,time,sys\nfrom hw5.worker import main\ndef stop(*args):\n print("SIGTERM: cleanup after preemption", flush=True)\n time.sleep(8)\n sys.exit(0)\nsignal.signal(signal.SIGTERM,stop)\nsys.argv=["worker","--prefix","runs/low"]\nwhile True: main()']
    apply(low, 'skills-low.yaml')
    def low_running():
        pods = pods_for('skills-low')
        if pods and pods[0]['status']['phase'] == 'Running':
            text = command(['logs', '-n', 'hw5', pods[0]['metadata']['name']], check=False).stdout
            if 'progress chunks=1 ' in text:
                return pods[0]
    original = wait_for(low_running)
    save('preemption-low-before.json', original)
    high = base_job('skills-high', 'runs/high', f'{chosen}Mi', '2000m', '2')
    high['spec']['template']['spec']['priorityClassName'] = 'hw5-high'
    apply(high, 'skills-high.yaml')
    low_name = original['metadata']['name']
    def disrupted():
        response = command(['get', 'pod', low_name, '-n', 'hw5', '-o', 'json'], check=False)
        if not response.returncode:
            pod = json.loads(response.stdout)
            if any(c['type'] == 'DisruptionTarget' and c['status'] == 'True' for c in pod['status'].get('conditions', [])):
                return pod
    victim = wait_for(disrupted, timeout=90)
    save('preemption-victim.json', victim)
    command(['get', 'events', '-n', 'hw5', '-o', 'json'], 'preemption-events.json')
    command(['logs', low_name, '-n', 'hw5'], 'preemption-low.log', check=False)
    finish_job('skills-high')
    command(['delete', 'job', 'skills-low', '-n', 'hw5', '--wait=true'], 'preemption-cleanup.log')
    save('preemption-summary.json', {**cpu, 'low_uid': original['metadata']['uid'],
                                     'low_request': low['spec']['template']['spec']['containers'][0]['resources']['requests']['cpu'],
                                     'high_request': '2000m', 'low_priority': 100, 'high_priority': 1000,
                                     'disruption': next(c for c in victim['status']['conditions'] if c['type'] == 'DisruptionTarget')})


def run_quota():
    command(['apply', '-f', 'manifests/quota.yaml'], 'quota-apply.log')
    first = task_pod('quota-first', '600m', 'hw5-quota')
    second = task_pod('quota-second', '600m', 'hw5-quota')
    apply(first, 'quota-first.yaml')
    second_path = GENERATED / 'quota-second.yaml'
    second_path.write_text(yaml.safe_dump(second, sort_keys=False))
    # Admission must reject the second Pod before it can ever become Pending.
    rejected = command(['create', '-f', str(second_path)], 'quota-rejected.log', check=False)
    assert rejected.returncode and 'exceeded quota' in rejected.stderr
    save('quota-at-rejection.json', get('resourcequota', 'compute-budget', 'hw5-quota'))
    command(['delete', 'pod', 'quota-first', '-n', 'hw5-quota', '--wait=true'], 'quota-release.log')
    wait_for(lambda: get('resourcequota', 'compute-budget', 'hw5-quota')['status']['used']['requests.cpu'] == '0')
    command(['create', '-f', str(second_path)], 'quota-accepted.log')
    wait_for(lambda: get('pod', 'quota-second', 'hw5-quota')['status']['phase'] == 'Running')
    save('quota-after-release.json', get('resourcequota', 'compute-budget', 'hw5-quota'))
    save('quota-accepted-pod.json', get('pod', 'quota-second', 'hw5-quota'))
    command(['delete', 'pod', 'quota-second', '-n', 'hw5-quota', '--wait=true'], 'quota-cleanup.log')


if __name__ == '__main__':
    os.chdir(ROOT)
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['all', 'parallel', 'scheduling', 'quota'], default='all')
    parser.add_argument('--reuse-parallel', action='store_true')
    args = parser.parse_args()
    chosen = run_memory() if args.phase == 'all' else json.loads((EVIDENCE / 'memory-summary.json').read_text())['chosen_limit_mib']
    if args.phase in ('all', 'parallel'):
        run_parallel(chosen, args.reuse_parallel)
    if args.phase in ('all', 'parallel', 'scheduling'):
        run_scheduling(chosen)
    if args.phase in ('all', 'parallel', 'scheduling', 'quota'):
        run_quota()
    command(['get', 'events', '-A', '-o', 'json'], 'events-final.json')
    print('All experiments verified', flush=True)
