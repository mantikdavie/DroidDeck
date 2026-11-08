#!/usr/bin/env python3
"""Compare the optional descriptor patch against pinned upstream recording behavior."""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('hybris_component', ROOT / 'tools/hybris/component.py')
component = importlib.util.module_from_spec(spec)
spec.loader.exec_module(component)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='local Git repository containing the pinned libhybris commit')
    parser.add_argument('--headers', type=Path, required=True, help='pinned Vulkan-Headers checkout')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('test output must be empty')
    if component.run(['git', '-C', str(args.headers), 'rev-parse', 'HEAD']).decode().strip() != component.revision(ROOT, True):
        raise ValueError('Vulkan-Headers revision differs from the test pin')
    out.mkdir(parents=True, exist_ok=True)
    source_pin = component.revision(ROOT)
    for variant in ('baseline', 'candidate'):
        source = out / variant / 'source'
        component.run(['git', 'clone', '-q', '--shared', '--no-checkout', str(args.source.resolve()), str(source)])
        component.run(['git', '-C', str(source), 'checkout', '-q', '--detach', source_pin])
        patches = [ROOT / 'tools/hybris/powervr-hal-version.patch',
                   *sorted((ROOT / 'tools/hybris/patches').glob('*.patch'))]
        if variant == 'candidate':
            patches.append(ROOT / 'tools/hybris/local-patches/bc-decode-descriptor-reuse.patch')
        for patch in patches:
            component.run(['git', '-C', str(source), 'apply', str(patch)])
        compat = source / 'hybris/vulkan/compat'
        binary = out / ('trace-' + variant)
        component.run([args.cc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
            '-ffunction-sections', '-fdata-sections', '-I' + str(args.headers.resolve() / 'include'),
            '-I' + str(compat), str(HERE / 'trace.c'), str(compat / 'bc_transfer.c'),
            str(compat / 'bc_decode.c'), str(compat / 'bc_image.c'),
            '-Wl,--gc-sections', '-o', str(binary)])
        (out / (variant + '.trace')).write_bytes(component.run([str(binary)]))
    subprocess.run([sys.executable, str(HERE / 'compare.py'), str(out)], check=True)


if __name__ == '__main__':
    main()
