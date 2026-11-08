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
