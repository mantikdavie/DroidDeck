# Full-width BC decode batching

`local-patches/bc-decode-batched-transfers.patch` is an optional combined patch:
it includes descriptor reuse and adds multirow decoding. Apply it **instead of**
`bc-decode-descriptor-reuse.patch`, after the normal repository patches. It is not
part of the default release patch series and changes no published component pin.

The new path requires an existing shared descriptor, the complete mip width,
zero X offset, a nonnegative block-aligned Y offset, and a width that fits the
existing four-row scratch allocation. It computes the largest multiple of four
rows fitting the same scratch buffer, then clamps the final batch to the remaining
height. Subrectangles, wide images and backings exceeding the descriptor range
retain the original four-row path. Packed BC4/RGB8 formats keep the existing
conservative four-byte capacity estimate. The decoder shader, decoded image
allocation, descriptor lifetime and synchronization rules are unchanged.

## Reproducible recording regression

Use Linux with a C compiler, Git, Python and zstd, a Git repository containing the
pinned libhybris commit, and a clean pinned Vulkan-Headers checkout. The runner
creates isolated source trees and verifies their exact declared patches. The
baseline is the existing descriptor-reuse patch; only the candidate batches rows.

```sh
python3 tools/hybris/tests/row_batching/run.py \
  --source /work/libhybris \
  --headers /work/Vulkan-Headers \
  --out /out/row-batching-regression
```

The existing Docker toolchain can run it with this repository mounted at `/repo`:

```sh
docker run --rm --platform linux/arm64 --cpus=2 --entrypoint python3 \
  -v "$PWD:/repo" -v /work:/work -v /out:/out -w /repo \
  droiddeck-digimon-dxvk-build:13.2-arm64 \
  tools/hybris/tests/row_batching/run.py \
  --source /work/libhybris --headers /work/Vulkan-Headers \
  --out /out/row-batching-regression
```

The trace harness reuses the descriptor regression's Vulkan stubs but compiles
the actual transfer, decoder preparation and image code. It independently checks
every pixel's absolute compressed block address and in-block index against its
destination image coordinates. Canonical row records must match, with complete
destination coverage and no overlapping intervals. It also verifies descriptor
bounds and immutable writes, each scratch read/write barrier pair, the outer
compressed-buffer barriers, contiguous dispatch chunks and the unchanged 64 KiB
maximum scratch capacity. This is a recording test, not native shader execution.

The 22 cases cover full widths from 128 to 4096, exact scratch capacity and tails,
odd widths/heights, partial Y stripes, mips/array layers, packed RGB8/BC4/RGB16F,
RGBA16F, small scratch/range limits, exact/over-limit buffers, subrectangles, wide
splits, source offsets above 4 GiB, small dispatch limits and consecutive regions.
The initial run checked 26,970,022 pixels per variant and matched 13,618 canonical
rows across 23 regions. Seven fallback cases retained all recorded command counts.

| Full-width RGBA8 case | Four-row copies | Batched copies |
| --- | ---: | ---: |
| 4096 × 4096 | 1024 | 1024 |
| 2048 × 2048 | 512 | 256 |
| 1024 × 1024 | 256 | 64 |
| 128 × 128 | 32 | 1 |
| 1024 × 17 | 5 | 2 |

Normally each copy has one dispatch and two scratch barriers. The artificial
one-workgroup dispatch limit still needs 256 dispatches for the 128 × 128 case,
while copies drop from 32 to one; the test checks this separately.

## Build and native acceptance

Follow [local component preparation](../../LOCAL_COMPONENT.md), substituting this
combined patch for the descriptor-only patch. From the resulting source checkout:

```sh
python3 tools/hybris/build_layer.py /out/hybris-row-batching \
  --source /work/libhybris --headers /work/Vulkan-Headers \
  --extra-patch tools/hybris/local-patches/bc-decode-batched-transfers.patch \
  --jobs 2
python3 tools/hybris/component.py verify /out/hybris-row-batching
```

Native acceptance must compare against the descriptor-only layer using identical
inputs and runtime. Exercise eligible small/odd/full-width images, mips, arrays,
partial uploads, repeated submissions and command-buffer reset/free. Retain the
existing scalar-packing probe's per-case categories: its known filtered-reference
differences are not a clean conformance pass. A 4096-wide RGBA8 smoke test alone
cannot validate the new multirow path because it still fits only four rows.

The initial native ARM64 Mesa comparison used synthetic BC1 blocks and a CPU
RGBA8 reference at 1024 × 1024, 128 × 128 and 9 × 7. Each layer passed all eight
sampling/render-target cases per size, with identical per-case readback hashes
and zero mismatches. The Pixel comparison subsequently passed the same 24 cases
per layer, with identical per-case readback hashes and no skipped cases.

The Pixel also completed 336 scalar-packing readbacks per layer across 14 BC
formats. All 412 READBACK/FAILURES/SUMMARY result lines matched exactly: BC/native,
nearest, raw-copy, sentinel and float-nearest discrepancies remained zero, while
both retained the same 6,837 existing filtered-reference discrepancies. This is
output equivalence, not a clean conformance pass. The probe covers mips, arrays,
partial copies, repeated submissions and command-buffer lifecycle operations.
These runs are correctness evidence; their process durations were not controlled
performance measurements.

The native synthetic probe source, generated shader files, build instructions,
input generator and hashes remain in the ignored `native-control/` artifact
directory. The checked-in reusable regression is the canonical coverage test
above. Pixel logs are under `build/device/digimon-perf-r8/`; the independently
checked result summaries are in
`build/device/digimon-bc-row-batching/independent-device-output-comparison.json`.

No game assets or captured shaders are included in this regression. Native test
artifacts and exact payload hashes belong under ignored
`build/device/digimon-bc-row-batching/`. Keep device tests temporary until output
equivalence and lifecycle tests pass; do not overwrite the installed runtime.

Fewer decode commands can reduce upload/loading or streaming overhead. Resident
frames without BC transfers do not use this path, and neither sustained FPS nor
lower texture residency follows from command-count reductions alone.
