"""Build the local Java 8 adapter without including vendor classes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from hmi_bench.config import Config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config.local.json')
    parser.add_argument('--jdk', type=Path, help='Existing JDK directory with bin/java and bin/javac')
    args = parser.parse_args()
    config = Config.load(args.config)
    for key in ('hmi_jar', 'osgi_jar'):
        if not config[key].is_file():
            parser.error('Missing separately supplied compile dependency: ' + key)
    suffix = '.exe' if os.name == 'nt' else ''
    compiler = args.jdk / ('bin/javac' + suffix) if args.jdk else shutil.which('javac')
    java = args.jdk / ('bin/java' + suffix) if args.jdk else shutil.which('java')
    if not compiler or not java or not Path(compiler).is_file() or not Path(java).is_file():
        parser.error('Supply --jdk for an existing JDK')
    source = ROOT / 'platform/BenchPlatform.java'
    build = ROOT / 'build'
    build.mkdir(exist_ok=True)
    config['adapter_jar'].parent.mkdir(parents=True, exist_ok=True)
    classpath = os.pathsep.join(str(config[key]) for key in ('hmi_jar', 'osgi_jar'))
    report = dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), passed=False, commands=[])
    try:
        with tempfile.TemporaryDirectory(prefix='platform-', dir=build) as temporary:
            directory = Path(temporary)
            classes = directory / 'classes'
            classes.mkdir()
            candidate = directory / 'bench-platform.jar'

            def execute(command):
                result = subprocess.run([str(item) for item in command], capture_output=True, timeout=90)
                row = dict(argv=[str(item) for item in command], exit_status=result.returncode,
                           stdout=result.stdout.decode(errors='replace'), stderr=result.stderr.decode(errors='replace'))
                report['commands'].append(row)
                if result.returncode:
                    raise RuntimeError(row['stdout'] + row['stderr'])
                return row['stdout']

            execute([compiler, '--release', '8', '-Xlint:-options', '-encoding', 'UTF-8', '-cp', classpath,
                     '-d', classes, source])
            with zipfile.ZipFile(candidate, 'w', compression=zipfile.ZIP_DEFLATED) as jar:
                for item in sorted(classes.rglob('*.class')):
                    name = item.relative_to(classes).as_posix()
                    if not name.startswith('bench/platform/') or int.from_bytes(item.read_bytes()[6:8], 'big') != 52:
                        raise ValueError('Expected only authored Java 8 bench/platform classes')
                    jar.write(item, name)
            output = execute([java, '-cp', str(candidate) + os.pathsep + classpath,
                              'bench.platform.BenchPlatform', '--self-test'])
            if 'SELF_TEST_PASS' not in output:
                raise RuntimeError('Missing adapter self-test success marker')
            backup = build / 'bench-platform.pre-vehicle.jar'
            if not backup.exists() and config['adapter_jar'].exists():
                shutil.copy2(config['adapter_jar'], backup)
            candidate.replace(config['adapter_jar'])
            report['passed'] = True
            print('Adapter built; original DSI callbacks, vehicle start/stop and pointer self-tests passed.')
    finally:
        (ROOT / 'reports').mkdir(exist_ok=True)
        (ROOT / 'reports/platform-build.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
