# Digimon Story Time Stranger: rendering and memory evidence

These findings concern Steam app 1984270 on the tested Google `kodiak` device.
They separate three failures that occurred at different stages of startup and play.

| Failure | Evidence | Scope of the change |
| --- | --- | --- |
| Initial allocation crash | The 39-bit native mapping layout prevented the game's contiguous 256 GiB virtual reservation. Relocating native PIE/interpreter mappings allowed both a native reservation probe and the game's own allocation to succeed. | PRoot compact layout, enabled by the Android host only for Google `kodiak`; no extra physical RAM is allocated by the reservation itself. |
| White/black game image | The captured Vulkan frame rendered correctly with Mesa but incorrectly with PowerVR. Changing only shader debug names restored rendering in controlled shader/frame comparisons. The guarded DXVK build subsequently displayed the actual game's colored 3D scene. | Omit generated shader debug names for `VK_DRIVER_ID_IMAGINATION_PROPRIETARY`. The option participates in the shader cache key. |
| Frozen rendering during texture uploads | Logs recorded `HYBRIS_BC transfer failed: -1`, `native=-5 recorded=-1`, and termination of DXVK's command-submission thread. The exact failed allocation site was not logged. | A local compatibility-layer candidate reduces descriptor allocation during BC decoding. This targets allocation pressure; the logs alone do not prove descriptor exhaustion was the sole cause. |

The first two changes are in the PRoot and DXVK patch series. The BC candidate is
an explicit local component, not a replacement published release pin. See
[local compatibility-layer packaging](../hybris/LOCAL_COMPONENT.md).

## BC descriptor reuse validation

For a compressed backing buffer within `maxStorageBufferRange`, the candidate
uses one immutable descriptor set per decode region and supplies the absolute
source offset through the existing push data. Larger buffers retain the original
per-tile path. Tile dimensions, decode shaders, copies and barriers are unchanged.

An independent mocked-call trace compiled the actual baseline/candidate transfer,
decode and image code. Twelve cases covered 4K textures, mips, layers, subrectangles,
wide images, the exact range limit, the fallback above it, source offsets above
4 GiB, and consecutive decode regions. All 8,611 semantic trace lines matched.
A 4K BC7 case reduced descriptor sets/updates from 1,024 to 1, while preserving
1,024 dispatches/copies and 2,050 barriers. The test does not emulate allocation
failures or native shader execution.

On the Pixel, baseline and candidate each completed 336 scalar-packing readbacks
across 14 BC formats. Every per-case result matched: BC/native comparison,
nearest sampling, raw copies, sentinel checks and float-nearest comparisons had
zero discrepancies. Both retained the same 6,837 filtered-reference discrepancies;
this was an equivalence result, not a clean conformance pass. A separate native
Mesa 4K BC1 fragment-sampling control passed all eight cases for both layers.

## Residency and budget limitations

Pinned hybris `bc_image.c` creates a full native decoded image with the requested
mips and layers and privately allocates its memory during BC image creation.
The compressed backing remains necessary for exact BC copies/readbacks. Both
allocations are released when the image is destroyed. For ordinary RGBA8 decode,
BC7 grows from approximately one compressed byte per pixel to an additional four
decoded bytes per pixel, before mip, layer and allocation-alignment costs.

The memory requirements exposed to DXVK describe the compressed backing. DXVK's
own allocation counters therefore omit the private decoded allocation. Descriptor
reuse reduces transient descriptor pressure; it does not reduce decoded texture
residency. The decode scratch buffer is capped at 64 KiB per command recording and
is retired with its command-buffer resources.

The recorded DXVK run reports `extMemoryBudget: 0`. In the pinned DXVK source,
`DxvkMemoryAllocator::updateMemoryHeapBudgets()` returns before applying
`dxvk.maxMemoryBudget` when that extension is disabled. Adapter budget reporting
then uses heap size and DXVK's own usage counters. `dxgi.maxDeviceMemory` changes
the adapter description, not `QueryVideoMemoryInfo` accounting. These settings
must not be presented as an accurate accounting fix for this runtime.

Useful follow-up instrumentation would count successful private decoded
allocations by their actual requirement size and heap, subtracting them on every
destruction/failure path. This would measure the missing residency; it would not
by itself establish a truthful global memory budget. Lazy allocation or mip
eviction would require additional image-view, descriptor, lifetime and
synchronization work and is not part of this candidate.

## Bounded game result

Texture quality was verified through the game UI: `tex_quality=2` is Low, while
the earlier `0` setting was High. With the guarded rendering fix, descriptor-reuse
candidate and verified Low textures, run `r6` retained a colored 3D scene and
working input for more than two minutes. A 25-second memory sample showed
2.16–2.25 GiB `MemAvailable` and zero game `VmSwap`. Separate 15-second display
pacing samples measured 17.4 and 23.2 FPS. Thermal status was `SEVERE` with a
41.1 °C battery reading; these measurements do not isolate a CPU/GPU bottleneck
or establish sustained 30 FPS. The later ARM64EC run also reached 3D, but its
different movement and thermal conditions do not establish a controlled speedup.
That ARM64EC experiment logged `Build: aarch64 clang 22.1.0`, remained
controllable as the user moved outdoors, and measured 16.4–26.2 FPS in separate
15-second samples. An outdoor sample measured 20.6 FPS, with 1.45–1.58 GiB
available memory and zero game swap. The user confirmed that the remaining
problem was low frame rate rather than completely frozen input. The packaged
candidate retains the guarded GCC DXVK build: these observations do not justify
claiming an ARM64EC speedup or changing the default architecture requirements.

Local diagnostic evidence is intentionally excluded from version control:
`build/device/digimon-guarded-r2-freeze/`,
`build/device/digimon-hybris-descriptor-reuse/{independent-review.md,mock/comparison.json}`,
`build/device/digimon-bc-reuse-r5-comparison.json`, and
`build/device/digimon-bc-r6-{steady-memory,thermal1,pacing1,pacing2}.json`.

## Optional lookup indexes and upload batching

The Vulkan compatibility layer also performs command-buffer and image lookups
while recording ordinary frames. Its original registries scan ownership lists,
including non-BC images. An opt-in diagnostic build sampled one lookup in 1,024
and emitted cumulative per-thread counts, traversal lengths and CPU/wall time.
It retained the original locking and used the same sampler for both variants.

`tools/hybris/local-patches/bc-indexed-lookups.patch` combines descriptor reuse
with fixed per-device command/image hash buckets. Ownership lists remain for
pool and device cleanup; all index mutations use the existing locks. It adds
10 KiB per device and one pointer per command/image node on AArch64, with no
additional allocation or failure path during lookup/insertion. Apply this patch
instead of the descriptor-only or batching alternatives, never on top of them.
The [lifecycle regression](../hybris/tests/indexed_lookups/README.md) exercises
actual registry entry points with mocked native Vulkan operations. Baseline and
candidate passed normal, ASan, UBSan and leak checks with identical outcomes,
including collisions, handle reuse, allocator failures, pool reset/destruction,
multi-device handles and concurrent operations on distinct objects.

On the Pixel, the indexed layer matched all 780 `BC_IMAGES_*` output lines from
the 336-readback baseline and passed 24 synthetic BC1 sampling/render-target
cases with identical readback hashes. The original 6,837 filtered-reference
discrepancies remained unchanged; this is equivalence, not full conformance.
Its formal local component and the tested DSO are byte-identical.

The diagnostic game samples measured the following per-call averages:

| Lookup | Baseline nodes | Indexed nodes | Baseline CPU time | Indexed CPU time |
| --- | ---: | ---: | ---: | ---: |
| Command buffer | 484.97 | 2.70 | 5.21 microseconds | 2.39 microseconds |
| Image | 1,875.40 | 2.47 | 35.32 microseconds | 2.40 microseconds |

These figures include timer and sampled node-counting overhead. The character
and camera moved during the indexed run, so the lookup populations differ.
The 20.3 versus 16.5 presented-FPS samples and 93.4% versus 51.6% command-thread
CPU samples are not controlled comparisons and establish neither a frame-rate
gain nor a regression. Battery readings were 40.6 and 41.3 degrees Celsius,
both with `SEVERE` thermal status. Session 50 is excluded entirely because
runtime-path verification found that session initialization had restored the
managed baseline. Only session 51 loaded the indexed diagnostic DSO. Both
compared game logs had no Vulkan error, BC transfer-end error, command-stream
exception or unhandled exception.

The separate [full-width batching candidate](../hybris/tests/row_batching/README.md)
combines descriptor reuse with larger decode batches within the existing 64 KiB
scratch buffer. Its canonical coverage regression, native Mesa control and Pixel
336-readback/24-synthetic-case comparisons passed equivalence checks. It reduces
upload command counts for eligible widths without changing decoded texture
residency. It is not included in the indexed candidate APK, and resident frames
without BC uploads cannot benefit directly from this batching path.

The indexed APK was built from `deeda030`, with guarded GCC DXVK unchanged.
Embedded component identity, source provenance, runtime inputs, native library
dependencies, alignment and v1/v2/v3 signatures passed verification. Diagnostic
DSOs and counters are excluded from that APK. Local evidence and the candidate
APK receipt are under ignored `build/device/digimon-perf-r8/`; reproducible
source regressions are checked in under `tools/hybris/tests/`.

## Fixed-camera follow-up (r9)

Sessions 52 (descriptor-reuse baseline) and 54 (indexed) loaded the same save
and used the same graphics settings, including Low textures and density. The
runtime checks confirmed the intended non-profile DSOs (`6addfed4...` and
`396552bd...`), no profiling override, identical graphics-file hashes and the
same `fps,cs` HUD. Before/after screenshots independently confirmed the same
alley geometry, camera and player position at both ends of each measurement. Idle
animations and NPC activity differed; this was not an identical-frame replay.
Session 53 was abandoned before measurement after the camera moved.

Each run recorded two consecutive display-pacing windows:

| Layer | Window | Presented frames / reported seconds | Reported FPS | Sampled GPU median |
| --- | ---: | ---: | ---: | ---: |
| Descriptor reuse | 1 | 387 / 20.1 | 19.3 | 911.5 MHz |
| Descriptor reuse | 2 | 325 / 20.1 | 16.2 | 748 MHz |
| Indexed | 1 | 451 / 20.1 | 22.5 | 1,094 MHz |
| Indexed | 2 | 405 / 20.0 | 20.2 | 940 MHz |

Combining the reported frame counts and rounded durations gives approximately
17.7 FPS for the baseline and 21.3 FPS for indexed. Separate ten-second samples
measured `dxvk-cs` at 78.2% and 54.4% of one CPU core respectively; both game
processes had zero swap. These are observations from this scene, not isolated
measurements of the patch's frame-rate or CPU benefit.

Hardware conditions were not fixed. Baseline GPU samples fell from 1,017 to
691 MHz; indexed samples ranged from 940 to 1,094 MHz and ended at 1,017 MHz.
Battery readings changed from 40.3 to 40.4 degrees Celsius for baseline and
40.6 to 40.8 for indexed. Both runs went from `MODERATE` to `SEVERE` thermal
status, while the recorded policy-6 CPU maximum was 4.013 GHz for baseline and
4.109 GHz for indexed. These readings do not establish why GPU frequency
changed, and maximum CPU limits do not describe actual per-thread clocks.
The higher indexed frame rate therefore cannot be attributed solely to lookup
indexing or generalized into a percentage speedup. The direct evidence for
indexing remains shorter lookup traversals, lower sampled per-call cost and
the equivalence/lifecycle regressions above.

Local screenshots, runtime identities, frame pacing, frequency samples, thermal
readings and thread samples are under ignored `build/device/digimon-perf-r9/`,
in the final `baseline-*` and `indexed-*` measurement files. Earlier loading
screens and filename-only "ready" labels are not measurement evidence.
