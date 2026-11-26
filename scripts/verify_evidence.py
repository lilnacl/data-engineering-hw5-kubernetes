"""Independent checks against Kubernetes snapshots and the original HW3 baseline."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads((ROOT / path).read_text())


def main():
    checks = {}
    source = read('evidence/source-upload.json')
    checks['source_sha256'] = source['source_sha256'] == read('baseline/source.json')['sha256']
    checks['s3_get_integrity'] = all(o['sha256'] == o['get_sha256'] and o['bytes'] == o['get_bytes'] for o in source['objects'])
    checks['partition_coverage'] = source['rows'] == 229388 and sum(o.get('rows', 0) for o in source['objects']) == 229388
    records = read('evidence/partition-record-integrity.json')
    checks['all_record_values_and_order_preserved'] = records['original'] == records['concatenated_parts'] and records['original']['rows'] == 229388
    checks['original_hw3_code'] = all(f['matches_hw3_commit'] and hashlib.sha256((ROOT / f['local_path']).read_bytes()).hexdigest() == f['sha256'] for f in read('evidence/hw3-provenance.json')['files'])
    memory = read('evidence/memory-summary.json')
    state = read('evidence/skills-oom-pods.json')[0]['status']['containerStatuses'][0]['state']['terminated']
    checks['real_oomkilled_137'] = state['reason'] == 'OOMKilled' and state['exitCode'] == 137
    required = max(64, ((memory['peak_bytes'] * 5 + 4 * 16 * 1024**2 - 1) // (4 * 16 * 1024**2)) * 16)
    checks['limit_from_measured_peak'] = required == memory['chosen_limit_mib']
    checks['tuned_completed'] = read('evidence/skills-tuned-job.json')['status']['succeeded'] == 1
    checks['all_pair_counts_match_hw3'] = read('results/counts_2.json') == read('baseline/counts_2.json')
    checks['top10_matches_hw3'] = read('results/top10.json') == sorted(read('baseline/counts_2.json'), key=lambda x: (-x['count'], x['skills']))[:10]
    shards = read('evidence/parallel-shards.json')
    checks['one_result_per_index'] = sorted(p['index'] for p in shards) == list(range(4))
    checks['parallel_source_coverage'] = sum(p['rows'] for p in shards) == 229388 and sum(p['selected_rows'] for p in shards) == 3123
    pods = read('evidence/skills-parallel-pods.json')
    intervals = [(c['state']['terminated']['startedAt'], c['state']['terminated']['finishedAt']) for p in pods for c in p['status']['containerStatuses'] if c['state'].get('terminated')]
    checks['four_workers_overlap'] = len(intervals) == 4 and max(i[0] for i in intervals) < min(i[1] for i in intervals)
    pending = read('evidence/cpu-pending.json')
    checks['pending_due_to_cpu'] = pending['status']['phase'] == 'Pending' and any(c['type'] == 'PodScheduled' and c['status'] == 'False' and 'Insufficient cpu' in c.get('message', '') for c in pending['status']['conditions'])
    checks['cpu_job_completed_after_release'] = read('evidence/skills-cpu-wait-job.json')['status']['succeeded'] == 1
    checks['same_cpu_pod_resumed'] = pending['metadata']['uid'] == read('evidence/skills-cpu-wait-pods.json')[0]['metadata']['uid']
    victim = read('evidence/preemption-victim.json')
    checks['scheduler_preemption'] = victim['metadata']['uid'] == read('evidence/preemption-low-before.json')['metadata']['uid'] and any(c['type'] == 'DisruptionTarget' and c['reason'] == 'PreemptionByScheduler' for c in victim['status']['conditions'])
    checks['high_priority_job_completed'] = read('evidence/skills-high-job.json')['status']['succeeded'] == 1
    rejected = (ROOT / 'evidence/quota-rejected.log').read_text()
    checks['quota_admission_rejected'] = 'Forbidden' in rejected and 'exceeded quota' in rejected and 'requests.cpu=600m' in rejected
    checks['quota_accepted_after_release'] = read('evidence/quota-accepted-pod.json')['status']['phase'] == 'Running'
    summary = {'passed': all(checks.values()), 'checks': checks}
    (ROOT / 'evidence/verification.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))
    assert summary['passed'], [key for key, value in checks.items() if not value]


if __name__ == '__main__':
    main()
