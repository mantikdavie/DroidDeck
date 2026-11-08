package com.droiddeck.launcher.runtime

object ProotAddressSpace {
    fun hostEnv(manufacturer: String, device: String): Map<String, String> =
        // Keep a contiguous 256 GiB reservation available on this 39-bit host.
        if (manufacturer.equals("Google", ignoreCase = true) && device == "kodiak") {
            mapOf("PROOT_COMPACT_VA" to "1")
        } else {
            emptyMap()
        }
}
