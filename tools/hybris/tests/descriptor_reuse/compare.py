from pathlib import Path
import hashlib
import json
import sys

root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
traces = {name: (root / (name + '.trace')).read_text().splitlines()
          for name in ('baseline', 'candidate')}
semantics = {name: [line for line in lines if not line.startswith('COUNTS ')]
             for name, lines in traces.items()}
assert semantics['baseline'] == semantics['candidate'], 'semantic call traces differ'

counts = {}
for variant, lines in traces.items():
    rows = {}
    for line in lines:
        if not line.startswith('COUNTS '):
            continue
        fields = dict(item.split('=', 1) for item in line.split()[1:])
        name = fields.pop('name')
        rows[name] = {key: int(value) for key, value in fields.items()}
    counts[variant] = rows
assert len(counts['baseline']) == len(counts['candidate']) == 12
fallback = {'limit-one-block-over', 'source-over-4gib'}
for name, before in counts['baseline'].items():
    after = counts['candidate'][name]
    for key in ('dispatches', 'copies', 'barriers'):
        assert before[key] == after[key]
    assert before['sets'] == before['updates'] == before['dispatches']
    if name in fallback:
        assert before == after
    else:
        assert after['sets'] == after['updates'] == (2 if name == 'consecutive-regions' else 1)

sources = [int(line.split(' source=', 1)[1].split()[0])
           for line in semantics['baseline'] if line.startswith('D ')]
assert max(sources) > 4 * 1024 ** 3
summary = {
    'result': 'PASS',
    'cases': len(counts['baseline']),
    'semantic_trace_lines_identical': len(semantics['baseline']),
    'semantic_trace_sha256': hashlib.sha256(('\n'.join(semantics['baseline']) + '\n').encode()).hexdigest(),
    'maximum_resolved_source_offset': max(sources),
    'bounds_assertions': 'explicit source/destination descriptor range, source buffer size, minStorageBufferOffsetAlignment, immutable descriptor writes, uint32 shader indexing via actual hybris_bc_prepare, dispatch sizes',
    'baseline': counts['baseline'],
    'candidate': counts['candidate'],
    'limitations': 'Mocked Vulkan calls; validates recording/address/range/lifetime equivalence, not native driver allocation pressure or shader execution.',
}
(root / 'comparison.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps({key: value for key, value in summary.items()
                  if key not in ('baseline', 'candidate')}, indent=2))
