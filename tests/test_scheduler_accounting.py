import json
from types import SimpleNamespace

from scripts import experiments


def test_cpu_budget_includes_pods_from_all_namespaces(monkeypatch):
    calls = []
    def kubectl(args, *rest, **kwargs):
        calls.append(args)
        if args[:2] == ['get', 'nodes']:
            result = {'items': [{'status': {'allocatable': {'cpu': '12'}}}]}
        elif '-A' in args:
            result = {'items': [
                {'spec': {'nodeName': 'node', 'containers': [{'resources': {'requests': {'cpu': '900m'}}}]}, 'status': {'phase': 'Running'}},
                {'spec': {'nodeName': 'node', 'containers': [{'resources': {'requests': {'cpu': '400m'}}}]}, 'status': {'phase': 'Running'}},
                {'spec': {'nodeName': 'node', 'containers': [{'resources': {'requests': {'cpu': '2'}}}]}, 'status': {'phase': 'Succeeded'}},
            ]}
        else:
            result = {'items': []}
        return SimpleNamespace(stdout=json.dumps(result))
    monkeypatch.setattr(experiments, 'command', kubectl)
    free, budget = experiments.free_cpu()
    assert free == 10700
    assert budget['existing_requests_millicpu'] == 1300
    assert any(args[:2] == ['get', 'pods'] and '-A' in args for args in calls)
