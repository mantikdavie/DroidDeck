# Optional indexed BC lookup regression

This test compiles the actual pinned `bc_context.c`, `bc_commands.c` and `bc_resources.c` with mocked native Vulkan calls and backend resource retirement. It compares the existing descriptor-reuse component with the optional indexed-lookup component. It contains no game shaders, captured assets or device dependencies.

The candidate patch, `tools/hybris/local-patches/bc-indexed-lookups.patch`, includes descriptor reuse. Apply it after the normal repository patches **instead of** `bc-decode-descriptor-reuse.patch` or `bc-decode-batched-transfers.patch`; do not stack these alternatives. It adds fixed per-device bucket heads while retaining the original ownership lists, locking order and allocator callbacks. On AArch64, bucket heads add 10 KiB per device and each command/image node adds one pointer. There is no new allocation during lookup or index insertion.

Run in a Linux environment with Git, Python 3, GCC, AddressSanitizer/UndefinedBehaviorSanitizer runtime libraries and the pinned Vulkan-Headers checkout:

```sh
python3 tools/hybris/tests/indexed_lookups/run.py \
  --source /work/libhybris --headers /work/Vulkan-Headers \
  --out /out/indexed-lookup-test
```

The output directory must be empty. The runner creates isolated Git checkouts, applies each declared patch alternative, verifies their source provenance, and leaves the supplied source and headers unchanged. It builds and runs baseline and candidate both normally and with AddressSanitizer, UndefinedBehaviorSanitizer and leak checking. The four outputs must match exactly. Commands, binary/source/patch hashes and validation scope are written to `comparison.json`; each execution retains stdout and stderr.

The lifecycle groups cover:

- 1,024 command buffers and 2,048 images, forcing bucket collisions without assuming the hash function; handles above 32 bits, two devices sharing the same image-handle value, non-BC images, deletion and handle reuse.
- Pool reset success and failure, pool destruction, unrelated pools remaining accessible, command begin/reset success and failure, and preservation of the original command node across state retirement.
- Device allocation, decoder creation, partial command-node allocation, native command allocation, image-node allocation and native/emulated image creation failures; custom allocator ownership/balance, image-specific allocator forwarding, device teardown and devices with no emulated formats.
- Four concurrent workers using separate externally synchronized pools and images, with 200 allocation/find/reset/free cycles each. The test does not issue concurrent operations on the same externally synchronized Vulkan object.

The registry, command and image entry points are the real implementation. GPU allocations, command journal contents and native Vulkan behavior are mocked; unexpected out-of-scope hooks abort. The concurrency case is a stress check, not ThreadSanitizer validation or a proof of all possible thread interleavings. Separate native baseline/candidate rendering probes and game measurements remain necessary. This regression does not measure performance.
