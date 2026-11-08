#ifndef DROIDDECK_PROOT_COMPACT_VA_H
#define DROIDDECK_PROOT_COMPACT_VA_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static inline uint64_t dd_proot_pic_address(uint64_t stock, unsigned elf_class,
                                           unsigned machine, int interpreter)
{
#if defined(__aarch64__)
    const char *enabled = getenv("PROOT_COMPACT_VA");
    if (elf_class == 2 && machine == 183 && enabled && strcmp(enabled, "1") == 0)
        return interpreter ? UINT64_C(0x6a00000000) : UINT64_C(0x6800000000);
#else
    (void) elf_class;
    (void) machine;
    (void) interpreter;
#endif
    return stock;
}

#endif
