from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys


def fields(line):
    return dict(item.split('=', 1) for item in line.split()[1:])


def read_trace(path):
    regions, rows, counts = {}, defaultdict(list), {}
    current = None
    for line in path.read_text().splitlines():
        if line.startswith('CASE '):
            data = fields(line)
            current = (data.pop('name'), int(data.pop('invocation')))
            assert current not in regions
            regions[current] = {key: int(value) for key, value in data.items()}
        elif line.startswith('R '):
            data = {key: int(value) for key, value in fields(line).items()}
            rows[current].append(tuple(data[key] for key in
                ('image', 'mip', 'layer', 'y', 'x', 'width', 'source', 'within_y', 'mode')))
        elif line.startswith('COUNTS '):
            data = fields(line)
            name = data.pop('name')
            counts[name] = {key: int(value) for key, value in data.items()}
    assert regions.keys() == rows.keys()
    for key, region in regions.items():
        intervals = defaultdict(list)
        for image, mip, layer, y, x, width, *_ in rows[key]:
            assert image == region['image'] and mip == region['mip']
            intervals[(layer, y)].append((x, x + width))
        expected = {(layer, y) for layer in range(region['layer'], region['layer'] + region['layers'])
                    for y in range(region['y'], region['y'] + region['height'])}
        assert intervals.keys() == expected, ('missing or unexpected output row', key)
        for segments in intervals.values():
            end = region['x']
            for start, next_end in sorted(segments):
                assert start == end and next_end > start, ('overlap or hole', key)
                end = next_end
            assert end == region['x'] + region['width']
        rows[key].sort()
    return regions, dict(rows), counts


def main():
    root = Path(sys.argv[1]).resolve()
    baseline = read_trace(root / 'baseline.trace')
    candidate = read_trace(root / 'candidate.trace')
    assert baseline[:2] == candidate[:2], 'canonical input/output row coverage differs'
    before, after = baseline[2], candidate[2]
    assert len(before) == len(after) == 22
    fallback = {'full-4096', 'small-scratch', 'limit-over-fallback', 'x-subrect-fallback',
                'width-subrect-fallback', 'width-over-fallback', 'source-over-4gib'}
    for name, old in before.items():
        new = after[name]
        regions = sum(key[0] == name for key in baseline[0])
        for value in (old, new):
            assert value['barriers'] == 2 * value['copies'] + 2 * regions
            assert value['scratch'] <= 65536
            assert value['sets'] == value['updates']
        for key in ('pixels', 'scratch', 'sets', 'updates', 'pools'):
            assert old[key] == new[key], (name, key)
        assert new['copies'] <= old['copies'] and new['dispatches'] <= old['dispatches']
        if name in fallback:
            assert old == new, ('fallback recording changed', name)
        else:
            assert new['copies'] < old['copies'], ('eligible case did not batch', name)
    for name, expected in {'full-2048': (512, 256), 'full-1024': (256, 64),
                           'full-128': (32, 1), 'scratch-exact': (4, 1),
                           'scratch-tail': (5, 2), 'odd-capacity': (9, 3),
                           'chunked-dispatch': (32, 1)}.items():
        assert (before[name]['copies'], after[name]['copies']) == expected
    assert before['chunked-dispatch']['dispatches'] == after['chunked-dispatch']['dispatches'] == 256
    canonical = [dict(name=key[0], invocation=key[1], rows=value)
                 for key, value in sorted(baseline[1].items())]
    encoded = json.dumps(canonical, separators=(',', ':')).encode()
    sources = [row[6] for rows in baseline[1].values() for row in rows]
    assert max(sources) > 4 * 1024 ** 3
    summary = {
        'result': 'PASS', 'cases': len(before), 'regions': len(baseline[0]),
        'canonical_rows': sum(map(len, baseline[1].values())),
        'pixels_checked_per_variant': sum(value['pixels'] for value in before.values()),
        'canonical_sha256': hashlib.sha256(encoded).hexdigest(),
        'maximum_resolved_source_offset': max(sources),
        'maximum_scratch_bytes': max(value['scratch'] for value in before.values()),
        'baseline': before, 'candidate': after,
        'limitations': 'Actual transfer/decode preparation with mocked Vulkan calls. '
                      'Checks every pixel source block/in-block index, complete destination coverage, '
                      'descriptor ranges and barrier ordering; does not execute the native shader '
                      'or simulate driver allocation failures.',
    }
    (root / 'comparison.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({key: value for key, value in summary.items()
                      if key not in ('baseline', 'candidate')}, indent=2))


if __name__ == '__main__':
    main()
