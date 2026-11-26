"""Generate report tables and excerpts only from the completed experiment snapshots."""
import json
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'report'


def read(name):
    return json.loads((ROOT / 'evidence' / name).read_text())


def tex(value):
    return str(value).replace('\\', '\\textbackslash{}').replace('_', '\\_').replace('&', '\\&').replace('%', '\\%').replace('#', '\\#')


def excerpt(filename, lines):
    wrapped = '\n'.join('\n'.join(textwrap.wrap(line, width=108, subsequent_indent='  ', break_long_words=False, break_on_hyphens=False)) for line in lines)
    (REPORT / filename).write_text('\\begin{Verbatim}[fontsize=\\scriptsize]\n' + wrapped + '\n\\end{Verbatim}\n')


def main():
    verification = read('verification.json')
    assert verification['passed'], 'Refusing to generate a success report from failed evidence'
    m = read('memory-summary.json')
    cpu = read('cpu-budget.json')
    preempt = read('preemption-summary.json')
    shards = read('parallel-shards.json')
    top = json.loads((ROOT / 'results/top10.json').read_text())
    facts = {
        'MemoryPeak': f'{m["peak_bytes"] / 1024**2:.2f}',
        'LimitMiB': str(m['chosen_limit_mib']),
        'TunedRSS': f'{m["tuned"]["memory"]["rss_peak_bytes"] / 1024**2:.2f}',
        'TunedCgroup': f'{m["tuned"]["memory"]["memory.peak"] / 1024**2:.2f}',
        'CPUAlloc': str(cpu['allocatable_millicpu']),
        'CPUUsed': str(cpu['existing_requests_millicpu']),
        'BlockingCPU': cpu['blocker_request'],
        'LowCPU': preempt['low_request'],
        'VerificationCount': str(len(verification['checks'])),
    }
    rows = '\n'.join(f'{i+1} & {p["memory"]["rss_peak_bytes"]/1024**2:.2f} & {p["memory"]["memory.peak"]/1024**2:.2f} & {p["seconds"]:.3f} \\\\' for i,p in enumerate(m['profiles']))
    facts['ProfileRows'] = rows
    facts['ShardRows'] = '\n'.join(f'{p["index"]} & {p["rows"]} & {p["selected_rows"]} & {p["s3_bytes"]} & {p["seconds"]:.3f} \\\\' for p in shards)
    facts['TopRows'] = '\n'.join(f'{i+1} & {tex(p["skills"][0])} & {tex(p["skills"][1])} & {p["count"]} & {p["count"]} \\\\' for i,p in enumerate(top))
    (REPORT / 'facts.tex').write_text('\n'.join('\\newcommand{\\' + key + '}{' + value + '}' for key,value in facts.items()) + '\n')
    oom_pod = read('skills-oom-pods.json')[0]
    terminated = oom_pod['status']['containerStatuses'][0]['state']['terminated']
    excerpt('oom-excerpt.tex', [f'pod={oom_pod["metadata"]["name"]}',
                             'requests.memory=32Mi  limits.memory=48Mi',
                             f'reason={terminated["reason"]}  exitCode={terminated["exitCode"]}',
                             f'startedAt={terminated["startedAt"]}  finishedAt={terminated["finishedAt"]}'])
    pending = read('cpu-pending.json')
    scheduled = next(c for c in pending['status']['conditions'] if c['type'] == 'PodScheduled')
    excerpt('cpu-excerpt.tex', [f'pod={pending["metadata"]["name"]}  phase={pending["status"]["phase"]}',
                             f'PodScheduled={scheduled["status"]}  reason={scheduled["reason"]}',
                             scheduled['message']])
    victim = read('preemption-victim.json')
    condition = next(c for c in victim['status']['conditions'] if c['type'] == 'DisruptionTarget')
    events = read('preemption-events.json')['items']
    event = next(e for e in events if e.get('reason') == 'Preempted' and e.get('involvedObject',{}).get('uid') == victim['metadata']['uid'])
    excerpt('preemption-excerpt.tex', [f'victim={victim["metadata"]["name"]}', f'uid={victim["metadata"]["uid"]}',
                                    f'DisruptionTarget={condition["status"]}  reason={condition["reason"]}',
                                    event['message']])
    quota = (ROOT / 'evidence/quota-rejected.log').read_text().strip()
    # Preserve the exact API error text, omitting only the local manifest path.
    message = quota[quota.index('pods "quota-second"'):]
    excerpt('quota-excerpt.tex', ['Error from server (Forbidden):', message])
    print('Report tables and excerpts generated from verified evidence')


if __name__ == '__main__':
    main()
