# Local DXVK component

`build.sh` builds the pinned `source.env` revision with every patch in `patches/`,
for both MinGW architectures. Run it in a Linux build environment with Git,
Python 3.9+, zstd, MinGW GCC/G++, Meson, Ninja and glslangValidator:

```sh
bash tools/dxvk/build.sh /absolute/path/to/empty/component
python3 tools/dxvk/component.py verify /absolute/path/to/empty/component
```

The output contains ten DLLs, the license, the complete patched source including
pinned submodules, the downstream diff, DLL checksums, and
`component-manifest.json`. The manifest records the current recipe hashes,
source revision and patched tree, toolchain versions, and every output hash.
Recording verifies the source bytes against the pinned Git tree plus the current
patches; unexpected source files or submodule changes are rejected. The source
archive and runtime assembly are deterministic for the same verified inputs.
This is local producer provenance, not a signature or attestation from upstream
or the published release's CI run.

Select that component explicitly when building the APK:

```sh
DROIDDECK_LOCAL_DXVK_COMPONENT=/absolute/path/to/empty/component \
  bash tools/build_local.sh
```

For runtime assembly without rebuilding the APK:

```sh
python3 tools/powervr/fetch.py --local-dxvk /absolute/path/to/empty/component
python3 tools/powervr/fetch.py --verify
```

The override replaces only the default `dxvk` component. Hybris, WSI and the
alternate Xclipse DXVK keep their published pins. Source/license files and the
local manifest travel inside the runtime, so the normal runtime-input and APK
checks can verify it without the original build directory. No release URLs or
published hashes are rewritten, and no artifacts are uploaded.

Without the explicit option, assembly uses published components and their
existing recipe pins. A changed recipe still fails verification until its
published pin is reviewed; a local build does not relax that check. After changing
a patch or recipe, rebuild the local component before using it again.

For an already built component with a preserved, verified source checkout, run
`component.py record COMPONENT --source CHECKOUT` inside its actual build
environment. This regenerates the provenance and source archive and requires the
same complete ten-DLL output. Keep unrelated build logs outside `COMPONENT`.

Focused checks:

```sh
python3 -m unittest discover -s tools/tests -p 'test_dxvk_component.py'
python3 -m unittest discover -s tools/tests -p 'test_powervr_runtime.py'
```
