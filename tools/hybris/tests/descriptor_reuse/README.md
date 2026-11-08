# Optional BC descriptor reuse regression

This test builds the actual pinned `bc_transfer.c`, `bc_decode.c` and `bc_image.c` twice against a mocked Vulkan resolver. The candidate applies the tracked `tools/hybris/local-patches/bc-decode-descriptor-reuse.patch`; the baseline applies only the normal repository patches. No game shaders or captured game assets are included.

Run in a Linux build environment with Git, Python 3, a C compiler and the pinned Vulkan-Headers checkout:

```sh
python3 tools/hybris/tests/descriptor_reuse/run.py \
  --source /work/libhybris --headers /work/Vulkan-Headers \
  --out /out/descriptor-reuse-test
```

The test creates isolated local Git clones and never modifies the supplied source. Its twelve cases compare every dispatch's resolved source address and shape, copied image region and barrier sequence. Assertions check descriptor bounds/alignment and immutable writes, 4K images, wide chunks, partial Y regions, mip/array layers, odd packed formats, exact storage-range limits, the old fallback immediately above that limit, offsets beyond 4 GiB, and consecutive decode regions. Allocation/update counts must decrease only on the shared path. It does not exercise actual Vulkan allocation pressure, command retirement or shader execution; native baseline/candidate rendering probes and game measurements remain necessary.
