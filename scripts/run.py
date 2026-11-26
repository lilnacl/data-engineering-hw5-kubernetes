"""Reproduce on a fresh isolated kind cluster without changing the default kubeconfig."""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('source', type=Path, help='Full it_vacancies_2024.csv from HW2/HW3')
    p.add_argument('--reuse-cluster', action='store_true', help='Resume setup after cluster creation on the same dedicated cluster')
    a = p.parse_args()
    source = a.source.resolve()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    work = root / 'work'
    work.mkdir(exist_ok=True)
    for tool in ('docker', 'kubectl', 'kind'):
        if not shutil.which(tool):
            raise SystemExit(f'{tool} is required; see README.md')
    env = {**os.environ, 'KUBECONFIG': str(work / 'kubeconfig'), 'PYTHONPATH': str(root),
           'BUILDX_CONFIG': str(work / 'buildx')}

    def run(args, log, input=None):
        print(' '.join(map(str, args)), flush=True)
        result = subprocess.run(args, input=input, text=True, capture_output=True, env=env)
        (root / 'evidence' / log).write_text((result.stdout + result.stderr).replace(str(root) + os.sep, ''))
        if result.returncode:
            raise SystemExit(f'Command failed; see evidence/{log}')

    if not a.reuse_cluster:
        run(['kind', 'create', 'cluster', '--name', 'de-hw5', '--config', 'manifests/kind.yaml',
             '--kubeconfig', env['KUBECONFIG'], '--wait', '180s'], 'cluster-create.log')
    run(['docker', 'build', '-t', 'de-hw5-skills:1', '.'], 'image-build.log')
    # Kubelet pulls pinned multi-architecture RustFS directly from the registry.
    # kind's --all-platforms import cannot load a partially cached Docker image index.
    for image in ('de-hw5-skills:1',):
        run(['kind', 'load', 'docker-image', image, '--name', 'de-hw5'], 'load-' + image.replace('/', '-').replace(':', '-') + '.log')
    run(['kubectl', 'apply', '-f', 'manifests/namespace.yaml'], 'namespaces.log')
    run(['kubectl', 'apply', '-f', 'manifests/priorities.yaml'], 'priorities-apply.log')
    credentials = {'access': 'hw5' + secrets.token_hex(8), 'secret': secrets.token_urlsafe(32)}
    credential_path = work / 'credentials.json'
    credential_path.write_text(json.dumps(credentials))
    credential_path.chmod(0o600)
    secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 's3-credentials', 'namespace': 'hw5'},
              'type': 'Opaque', 'stringData': credentials}
    run(['kubectl', 'create', '-f', '-'], 'secret-created.log', json.dumps(secret))
    run(['kubectl', 'apply', '-f', 'manifests/rustfs.yaml'], 'rustfs-apply.log')
    run(['kubectl', 'rollout', 'status', 'deployment/rustfs', '-n', 'hw5', '--timeout=180s'], 'rustfs-ready.log')
    for resource, name in [('nodes', 'nodes.json'), ('pvc,pv', 'storage-volumes.json')]:
        run(['kubectl', 'get', resource, '-A', '-o', 'json'], name)
    run(['kubectl', 'version', '-o', 'json'], 'kubernetes-version.json')
    env.update({'S3_ENDPOINT': 'http://127.0.0.1:19000',
                'S3_ACCESS_KEY': credentials['access'], 'S3_SECRET_KEY': credentials['secret']})
    with (work / 'port-forward.log').open('w') as log:
        forward = subprocess.Popen(['kubectl', 'port-forward', '-n', 'hw5', 'service/rustfs',
                                    '19000:9000', '--address', '127.0.0.1'], env=env, stdout=log, stderr=log)
        try:
            for _ in range(50):
                if forward.poll() is not None:
                    raise RuntimeError('Port-forward exited; see work/port-forward.log')
                if 'Forwarding from' in (work / 'port-forward.log').read_text():
                    break
                time.sleep(0.2)
            run([sys.executable, 'scripts/upload_data.py', str(source)], 'source-upload.log')
            run([sys.executable, 'scripts/experiments.py'], 'experiments.log')
            run([sys.executable, '-m', 'pytest', 'tests', '-q'], 'tests.log')
            run([sys.executable, 'scripts/verify_evidence.py'], 'verification.log')
        finally:
            forward.terminate()
            forward.wait(timeout=10)
    print('Experiments verified. The cluster remains available for inspection.', flush=True)


if __name__ == '__main__':
    main()
