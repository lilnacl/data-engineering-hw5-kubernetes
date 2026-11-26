import csv
import io
import json
from collections import Counter

import pytest

from hw5.pipeline import count_stream, merge_shards, memory_limit_mib, split_csv, top10


def test_multiline_csv_is_partitioned_by_records(tmp_path):
    source = tmp_path / 'source.csv'
    rows = [
        {'name': 'Go\nDeveloper', 'role': 'Программист, разработчик', 'key_skills': 'Go;SQL;Go'},
        {'name': 'Django', 'role': 'Программист, разработчик', 'key_skills': 'Python;SQL'},
        {'name': 'Backend', 'role': 'Программист, разработчик', 'key_skills': 'Golang; Docker;SQL'},
        {'name': 'Go analyst', 'role': 'Аналитик', 'key_skills': 'Go;SQL'},
        {'name': 'Go developer', 'role': 'Программист, разработчик', 'key_skills': ''},
    ]
    with source.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    parts = split_csv(source, tmp_path / 'parts', 4)
    restored = []
    counts = Counter()
    selected = 0
    for part in parts:
        with open(part['path'], newline='') as f:
            restored.extend(csv.DictReader(f))
        with open(part['path'], 'rb') as f:
            result = count_stream(f)
        counts.update(result[0])
        selected += result[1]['selected_rows']
    assert restored == rows
    assert sum(p['rows'] for p in parts) == 5
    assert selected == 3
    assert counts == {('go', 'sql'): 2, ('docker', 'go'): 1, ('docker', 'sql'): 1}


def test_merge_requires_exactly_one_result_per_index():
    parts = [
        {'index': 0, 'counts': [{'skills': ['go', 'sql'], 'count': 2}], 'rows': 3, 'selected_rows': 2},
        {'index': 1, 'counts': [{'skills': ['go', 'sql'], 'count': 4}], 'rows': 4, 'selected_rows': 4},
    ]
    result = merge_shards(parts, 2)
    assert result['counts'] == [{'skills': ['go', 'sql'], 'count': 6}]
    assert result['rows'] == 7
    with pytest.raises(ValueError, match='indices'):
        merge_shards([parts[0], parts[0]], 2)
    with pytest.raises(ValueError, match='indices'):
        merge_shards(parts[:1], 2)


def test_top10_is_deterministic_for_ties():
    counts = Counter({('go', 'sql'): 4, ('docker', 'go'): 4, ('a', 'b'): 5})
    assert [x['skills'] for x in top10(counts)] == [['a', 'b'], ['docker', 'go'], ['go', 'sql']]


def test_memory_limit_uses_peak_and_25_percent_headroom():
    assert memory_limit_mib(80 * 1024**2) == 112
    assert memory_limit_mib(1) == 64
    assert memory_limit_mib(128 * 1024**2) == 160
    with pytest.raises(ValueError):
        memory_limit_mib(0)
