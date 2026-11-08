# Local compatibility-layer components

`tools/hybris/build_layer.py` builds the Vulkan compatibility layer from verified local source. The resulting component is explicitly labelled `local-compat-layer`. The APK assembler combines it with the existing pinned hybris bridge, replacing only `lib/libVkLayer_hybris_compat.so`. The HAL wrapper, Android linker and other bridge libraries retain their original release provenance.

The producer requires an AArch64 Linux environment with GCC, binutils, Git, Python 3 and zstd. Use a checkout at `HYBRIS_REVISION` from `source.env`, with `powervr-hal-version.patch`, all repository `patches/*.patch`, and one explicit local patch applied in that order. Keep the local patch and build output outside the source checkout. Vulkan-Headers must be a clean checkout at `VULKAN_HEADERS_REVISION` from `layer-source.env`.

The optional BC descriptor reuse change is preserved in `local-patches/bc-decode-descriptor-reuse.patch`. It is not in the automatically applied release patch directory. With this repository mounted at `/repo`, prepare its source explicitly:

```sh
. /repo/tools/hybris/source.env
. /repo/tools/hybris/layer-source.env
git clone https://github.com/taowen/libhybris.git /work/libhybris
git -C /work/libhybris checkout --detach "$HYBRIS_REVISION"
git clone https://github.com/KhronosGroup/Vulkan-Headers.git /work/Vulkan-Headers
git -C /work/Vulkan-Headers checkout --detach "$VULKAN_HEADERS_REVISION"
git -C /work/libhybris apply /repo/tools/hybris/powervr-hal-version.patch
git -C /work/libhybris apply /repo/tools/hybris/patches/*.patch
git -C /work/libhybris apply /repo/tools/hybris/local-patches/bc-decode-descriptor-reuse.patch
```

```sh
python3 tools/hybris/build_layer.py /out/hybris-local \
  --source /work/libhybris \
  --headers /work/Vulkan-Headers \
  --extra-patch tools/hybris/local-patches/bc-decode-descriptor-reuse.patch \
  --jobs 2
python3 tools/hybris/component.py verify /out/hybris-local
```

The producer checks the exact source blobs against the declared patch sequence before and after compiling. It uses the upstream compatibility-layer source list, preserves `NODELETE`, and rejects undefined symbols. Its output includes the layer, all upstream libhybris licenses, complete patched libhybris source, complete Vulkan-Headers source, the combined and local patches, pinned revisions, compiler/target/linker/strip/zstd versions, compile/link flags, source hashes and a canonical component manifest. A recipe change requires rebuilding the component. The explicit patch is local-build provenance; it does not become a published release pin.

To select the component for APK assembly:

```sh
python3 tools/powervr/fetch.py --local-hybris /out/hybris-local
```

It can be combined with `--local-dxvk /out/dxvk-local`. The normal local APK build accepts both selections through environment variables:

```sh
DROIDDECK_LOCAL_HYBRIS_COMPONENT=/out/hybris-local \
DROIDDECK_LOCAL_DXVK_COMPONENT=/out/dxvk-local \
bash tools/build_local.sh
```

The assembler retains the original compatibility-layer bytes inside the runtime. Verification restores those bytes in a temporary in-memory view and checks the complete original bridge tree against its release pin, while checking the replacement and its provenance separately. `fetch.py --verify` reads the embedded manifests, so it does not depend on the producer's local output directory remaining available. No local component is selected unless explicitly supplied.

Packaging and provenance checks establish the identity of the built source and payload. Native rendering, memory use and game performance require their own recorded validation before recommending a local driver change.

The descriptor change reuses one immutable descriptor set per decode region when the whole compressed buffer fits the storage-buffer range limit, retaining the old fallback otherwise. It does not change four-row slicing or reduce decoded texture residency. Its standalone regression is documented in `tests/descriptor_reuse/README.md`. Native probe output matched the baseline; the observed successful game run also used corrected Low graphics settings, so a standalone game benefit from descriptor reuse has not been isolated.
