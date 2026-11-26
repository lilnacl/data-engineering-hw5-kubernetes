import csv
import hashlib
import math
from collections import Counter
from contextlib import ExitStack
from pathlib import Path

from hw3.mapreduce import S3Raw, csv_chunks, map_chunk


def encode_counts(counts):
    return [{'skills': list(key), 'count': value} for key, value in sorted(counts.items())]


def count_stream(raw):
    stream = S3Raw(raw)
    counts = Counter()
    selected = chunks = 0
    for rows in csv_chunks(stream, 2000):
        mapped, n, _ = map_chunk(rows, combine=True)
        counts.update(mapped[1])
        selected += n
        chunks += 1
        if chunks == 1 or chunks % 25 == 0:
            print(f'progress chunks={chunks} rows={stream.csv_rows} selected={selected}', flush=True)
    return counts, {'rows': stream.csv_rows, 'selected_rows': selected,
                    'chunks': chunks, 's3_bytes': stream.bytes_read}


def split_csv(source, destination, count):
    if count < 1:
        raise ValueError('count must be positive')
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    csv.field_size_limit(16 * 1024 * 1024)
    # First pass counts CSV records, including records with quoted newlines.
    with source.open(encoding='utf-8-sig', newline='') as f:
        total = sum(1 for _ in csv.DictReader(f))
    paths = [destination / f'part-{i:02d}.csv' for i in range(count)]
    sizes = [total // count + (i < total % count) for i in range(count)]
    with source.open(encoding='utf-8-sig', newline='') as f, ExitStack() as stack:
        reader = csv.DictReader(f)
        for index, path in enumerate(paths):
            out = stack.enter_context(path.open('w', encoding='utf-8', newline=''))
            writer = csv.DictWriter(out, fieldnames=reader.fieldnames)
            writer.writeheader()
            for _ in range(sizes[index]):
                writer.writerow(next(reader))
    start = 0
    parts = []
    for index, path in enumerate(paths):
        parts.append({'index': index, 'path': str(path), 'rows': sizes[index],
                      'start_record': start, 'end_record_exclusive': start + sizes[index],
                      'bytes': path.stat().st_size, 'sha256': file_sha256(path)})
        start += sizes[index]
    return parts


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        while block := f.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def merge_shards(parts, count):
    indices = [part['index'] for part in parts]
    if sorted(indices) != list(range(count)):
        raise ValueError(f'incomplete or duplicate indices: {indices}')
    counts = Counter()
    for part in parts:
        counts.update({tuple(item['skills']): item['count'] for item in part['counts']})
    return {'counts': encode_counts(counts), 'top10': top10(counts),
            'rows': sum(part['rows'] for part in parts),
            'selected_rows': sum(part['selected_rows'] for part in parts),
            'distinct_pairs': len(counts), 'pair_occurrences': sum(counts.values())}


def top10(counts):
    return [{'skills': list(key), 'count': value}
            for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]]


def memory_limit_mib(peak_bytes):
    if peak_bytes <= 0:
        raise ValueError('peak must be positive')
    return max(64, math.ceil(peak_bytes * 1.25 / (16 * 1024**2)) * 16)
