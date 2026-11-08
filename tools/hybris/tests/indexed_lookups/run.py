#!/usr/bin/env python3
"""Compare BC registry lifecycles with native Vulkan calls replaced by mocks."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('hybris_component', ROOT / 'tools/hybris/component.py')
component = importlib.util.module_from_spec(spec)
spec.loader.exec_module(component)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--headers', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('test output must be empty')
    component.snapshot(args.headers.resolve(), component.revision(ROOT, True))
    out.mkdir(parents=True, exist_ok=True)
    outputs, builds = {}, []
    for variant, name in (('baseline', 'bc-decode-descriptor-reuse.patch'),
                          ('candidate', 'bc-indexed-lookups.patch')):
        source = out / variant / 'source'
        component.run(['git', 'clone', '-q', '--shared', '--no-checkout', str(args.source.resolve()), str(source)])
        component.run(['git', '-C', str(source), 'checkout', '-q', '--detach', component.revision(ROOT)])
        local = ROOT / 'tools/hybris/local-patches' / name
        for patch in [ROOT / 'tools/hybris/powervr-hal-version.patch',
                      *sorted((ROOT / 'tools/hybris/patches').glob('*.patch')), local]:
            component.run(['git', '-C', str(source), 'apply', str(patch)])
        component.source_snapshot(source, args.headers.resolve(), local.read_bytes())
        for mode in ('normal', 'sanitized'):
            binary = out / f'{variant}-{mode}'
            flags = ['-O2'] if mode == 'normal' else ['-O1', '-g', '-fno-omit-frame-pointer',
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
            command = [args.cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pthread',
                '-ffunction-sections', '-fdata-sections', *flags,
                '-I' + str(args.headers.resolve() / 'include'),
                '-I' + str(source / 'hybris/vulkan/compat'), str(HERE / 'lifecycle.c'),
                '-Wl,--gc-sections', '-o', str(binary)]
            try:
                component.run(command)
            except subprocess.CalledProcessError as error:
                raise RuntimeError(error.stderr.decode(errors='replace')) from error
            env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
                       UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
            result = subprocess.run([str(binary)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=env, timeout=90)
            (out / f'{variant}-{mode}.stdout').write_bytes(result.stdout)
            (out / f'{variant}-{mode}.stderr').write_bytes(result.stderr)
            if result.returncode or result.stderr:
                raise RuntimeError(f'{variant}/{mode} failed ({result.returncode}): '
                                   + result.stderr.decode(errors='replace'))
            assert result.stdout.count(b'PASS ') == 4 and b'SUMMARY groups=4 ' in result.stdout
            outputs[f'{variant}-{mode}'] = result.stdout
            builds.append({'variant': variant, 'mode': mode, 'command': command,
                           'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()})
    assert len(set(outputs.values())) == 1, 'baseline/candidate lifecycle output differs'
    summary = {'result': 'PASS', 'groups': 4, 'builds': builds,
               'output': next(iter(outputs.values())).decode(),
               'source_test_sha256': hashlib.sha256((HERE / 'lifecycle.c').read_bytes()).hexdigest(),
               'candidate_patch_sha256': hashlib.sha256((ROOT / 'tools/hybris/local-patches/bc-indexed-lookups.patch').read_bytes()).hexdigest(),
               'scope': 'Actual context/command/image registry code, mocked native Vulkan and backend resource retirement. '
                        'Baseline/candidate both pass normally and under AddressSanitizer/UndefinedBehaviorSanitizer with leak checks. '
                        'Four concurrent workers use different externally synchronized pools/images. Not a ThreadSanitizer, GPU, or performance test.'}
    (out / 'comparison.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'builds'}, indent=2))


if __name__ == '__main__':
    main()
