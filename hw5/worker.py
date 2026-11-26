import argparse
import json
import os
import resource
import time
from pathlib import Path

from hw5.pipeline import count_stream, encode_counts, merge_shards
from hw5.storage import client, get_json, put_json


def memory():
    values = {'rss_peak_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024}
    for name in ('memory.peak', 'memory.current', 'memory.max', 'memory.events'):
        path = Path('/sys/fs/cgroup') / name
        if path.exists():
            value = path.read_text().strip()
            values[name] = int(value) if value.isdigit() else value
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['count', 'reduce'], default='count')
    parser.add_argument('--key', default='raw/it_vacancies_2024.csv')
    parser.add_argument('--prefix', default='runs/parallel')
    parser.add_argument('--parts', type=int, default=4)
    args = parser.parse_args()
    s3 = client()
    if args.mode == 'reduce':
        shards = [get_json(s3, f'{args.prefix}/part-{i:02d}.json') for i in range(args.parts)]
        result = merge_shards(shards, args.parts)
        baseline = json.loads(Path('/baseline/counts_2.json').read_text())
        result['baseline_equal'] = result['counts'] == baseline
        if not result['baseline_equal']:
            raise ValueError('Full pair frequencies differ from HW3')
        put_json(s3, f'{args.prefix}/merged.json', result)
        print(json.dumps({k: v for k, v in result.items() if k != 'counts'}, ensure_ascii=False), flush=True)
        return
    index = int(os.getenv('JOB_COMPLETION_INDEX', '0'))
    key = args.key.format(index=index)
    started = time.monotonic()
    print(json.dumps({'event': 'start', 'key': key, 'index': index, 'memory': memory()}), flush=True)
    response = s3.get_object(Bucket='hh-lake', Key=key)
    with response['Body'] as body:
        counts, stats = count_stream(body)
    result = {'index': index, 'source_key': key, **stats, 'counts': encode_counts(counts),
              'seconds': time.monotonic() - started, 'memory': memory()}
    key_out = f'{args.prefix}/part-{index:02d}.json'
    put_json(s3, key_out, result)
    # Capture serialization and the first S3 upload in the cgroup high watermark too.
    result['memory'] = memory()
    put_json(s3, key_out, result)
    print(json.dumps({k: v for k, v in result.items() if k != 'counts'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
