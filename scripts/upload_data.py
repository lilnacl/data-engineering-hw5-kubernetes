"""Upload the original source and four record-aligned files; verify each by GET."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from hw5.pipeline import file_sha256, split_csv
from hw5.storage import client, put_json


def logical_records(files):
    digest = hashlib.sha256()
    rows = 0
    for path in files:
        with Path(path).open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                digest.update((json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode())
                rows += 1
    return {'rows': rows, 'sha256_logical_records': digest.hexdigest()}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('source', type=Path)
    p.add_argument('--work', type=Path, default=Path('work/data'))
    a = p.parse_args()
    baseline = json.loads(Path('baseline/source.json').read_text())
    expected = baseline.get('sha256_s3_get', baseline.get('sha256'))
    actual = file_sha256(a.source)
    if actual != expected:
        raise ValueError(f'Wrong source SHA-256: {actual}')
    s3 = client()
    s3.create_bucket(Bucket='hh-lake')
    parts = split_csv(a.source, a.work, 4)
    original = logical_records([a.source])
    concatenated = logical_records([part['path'] for part in parts])
    assert original == concatenated, 'CSV partitioning changed values or record order'
    integrity = {'original': original, 'concatenated_parts': concatenated,
                 'all_23_columns_and_record_order_preserved': True}
    Path('evidence/partition-record-integrity.json').write_text(json.dumps(integrity, indent=2) + '\n')
    objects = [{'path': str(a.source), 'key': 'raw/it_vacancies_2024.csv', 'sha256': actual,
                'bytes': a.source.stat().st_size},
               *[{**part, 'key': f'parts/part-{part["index"]:02d}.csv'} for part in parts]]
    for item in objects:
        s3.upload_file(item['path'], 'hh-lake', item['key'])
        response = s3.get_object(Bucket='hh-lake', Key=item['key'])
        digest = hashlib.sha256()
        size = 0
        with response['Body'] as body:
            while block := body.read(1024 * 1024):
                digest.update(block)
                size += len(block)
        item['get_bytes'] = size
        item['get_sha256'] = digest.hexdigest()
        assert item['get_bytes'] == item['bytes'] and item['get_sha256'] == item['sha256']
        print(json.dumps({k: v for k, v in item.items() if k != 'path'}, ensure_ascii=False), flush=True)
    # Local paths and credentials never enter the public manifest.
    manifest = {'source_sha256': actual, 'objects': [{k: v for k, v in obj.items() if k != 'path'} for obj in objects],
                'rows': sum(part['rows'] for part in parts), 'parts': 4}
    Path('evidence/source-upload.json').write_text(json.dumps(manifest, indent=2) + '\n')
    put_json(s3, 'parts/manifest.json', manifest)


if __name__ == '__main__':
    main()
