# Vulkan debug-name regression

This standalone diagnostic checks that SPIR-V debug names cannot change rendered pixels. It was developed after a PowerVR shader compiler rendered incorrect UVs when a vertex input and output had the same `OpName` string.

The test contains only original GLSL and a synthetic quad. It has no game shaders, assets, uniforms, downloads or account dependencies. It renders offscreen through the selected Vulkan ICD and compares every pixel of a 64×32 RGBA8 UV gradient.

## Build

A Linux C compiler, Vulkan headers, Python3, `glslangValidator` and `spirv-val` are required. For example, Ubuntu provides them in `build-essential libvulkan-dev python3 glslang-tools spirv-tools`.

From the repository root:

```sh
sh tools/tests/vulkan_debug_names/build.sh build/vulkan-debug-names
```

The existing local ARM64 diagnostic builder can also be used:

```sh
docker run --rm --platform linux/arm64 \
  -v "$PWD:/repo" -w /repo droiddeck-bc-shader-tools:probe \
  sh tools/tests/vulkan_debug_names/build.sh build/vulkan-debug-names
```

The executable embeds all shader variants; no companion files are needed at runtime. Generated SPIR-V and a hash manifest remain in the output directory for inspection. Build outputs belong outside this source directory.

## Run

Vulkan1.3 with dynamic rendering and a graphics queue is required. The probe uses the first physical device reported by the selected ICD. Set `VK_DRIVER_FILES` or the normal application's ICD/layer environment to select the intended driver.

```sh
build/vulkan-debug-names/vulkan-debug-names
build/vulkan-debug-names/vulkan-debug-names --case no_names
```

Available cases are `original`, `same_name`, `unique_names` and `no_names`. A conformant driver should pass all four. Exit0 means every selected case passed; exit1 means pixels differed; exit2 means an argument, loader, capability or Vulkan operation failed. A 60-second watchdog terminates a stalled run; that is not a pass.

For a native ARM64 lavapipe control with the existing builder:

```sh
docker run --rm --platform linux/arm64 \
  -v "$PWD:/repo" -w /repo \
  -e VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.json \
  droiddeck-bc-shader-tools:probe build/vulkan-debug-names/vulkan-debug-names
```

In a DroidDeck Linux guest, run the executable under its existing managed Vulkan environment. No WSI or BC texture support is required by the test. The probe explicitly writes no files and reports results to stdout; the Vulkan driver may use its normal shader cache.

## Controls and interpretation

All four vertex modules share the same result IDs, header bound and non-name instruction words:

| Case | UV input/output debug names |
|---|---|
| `original` | `uv_in`, `uv_out` |
| `same_name` | Both `TEXCOORD` |
| `unique_names` | `INPUT_UV`, `OUTPUT_UV` |
| `no_names` | All `OpName`/`OpMemberName` instructions removed |

The generator edits only name instructions and verifies equality of every remaining instruction. Each variant and the fragment shader pass `spirv-val` during the build. Pipeline state, vertex bytes, input locations, fragment shader, viewport and readback are identical across cases. All2048pixels per case are compared with the expected gradient, allowing one RGBA8 channel LSB for conversion rounding. A blue clear value distinguishes absent geometry from a valid gradient.

The [Khronos SPIR-V specification](https://registry.khronos.org/SPIR-V/specs/unified1/SPIRV.html#OpName) defines `OpName` and `OpMemberName` as nonsemantic instructions that can be removed. Passing unique/removed-name controls while failing only `same_name` isolates a driver/compiler dependency on debug naming, rather than a difference in shader calculations or interface locations.

This is an opt-in driver diagnostic, not an automatic test-suite dependency. The current simplified harness passes all four cases on both lavapipe and the tested PowerVR device. An earlier, larger synthetic harness reproduces the duplicate-name failure, so a necessary trigger was removed during simplification. This version must not yet be presented as a standalone reproducer of that driver defect. The original all-pass device result is preserved at `build/vulkan-debug-names/pixel.json`. Shader-byte and pipeline-feature comparisons continue in ignored build artifacts. A workaround limited to DXVK does not change this standalone native Vulkan path.
