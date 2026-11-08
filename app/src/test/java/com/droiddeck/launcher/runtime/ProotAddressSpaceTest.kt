package com.droiddeck.launcher.runtime

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ProotAddressSpaceTest {
    @Test fun googleKodiakUsesCompactNativeMappings() {
        for (manufacturer in listOf("Google", "google", "GOOGLE")) {
            assertEquals(mapOf("PROOT_COMPACT_VA" to "1"), ProotAddressSpace.hostEnv(manufacturer, "kodiak"))
        }
    }

    @Test fun otherGoogleDevicesKeepTheDefaultLayout() {
        for (device in listOf("caiman", "komodo", "Pixel 11 Pro XL", "kodiak-pro", "")) {
            assertTrue(device, ProotAddressSpace.hostEnv("Google", device).isEmpty())
        }
    }

    @Test fun theCodenameAloneDoesNotEnableTheWorkaround() {
        for (manufacturer in listOf("Samsung", "Qualcomm", "unknown", "")) {
            assertTrue(manufacturer, ProotAddressSpace.hostEnv(manufacturer, "kodiak").isEmpty())
        }
    }
}
