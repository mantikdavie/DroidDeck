package com.droiddeck.launcher.gpu

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SystemVulkanHalTest {
    private val legacy = "/vendor/lib64/hw/vulkan.powervr.so"

    private fun select(
        files: Set<String>, hardware: String = "", platform: String = "", apex: String = "",
        mali: Boolean = false, vendor: () -> String? = { error("Vulkan query was not needed") },
    ): String? = SystemVulkanDriver.findHal(hardware, platform, apex, mali, { it in files }, vendor)

    @Test fun legacyPowerVrKeepsItsSystemDriverWithoutQueryingVulkan() {
        assertEquals(legacy, select(setOf(legacy)))
        assertEquals(legacy, select(setOf(legacy), hardware = "powervr"))
        assertNull(select(emptySet()))
    }

    @Test fun connectedPixelPropertiesSelectTheVendorPowerVrHal() {
        assertEquals(legacy, select(setOf(legacy), hardware = "powervr", platform = "malibu"))
    }

    @Test fun softwareSessionGlIsLimitedToTheVerifiedPixelPowerVrCombination() {
        assertTrue(SystemVulkanDriver.softwareSessionGl("Google", "kodiak", legacy))
        assertTrue(SystemVulkanDriver.softwareSessionGl("google", "kodiak",
            "/apex/com.vendor.graphics/lib64/hw/vulkan.powervr.so"))
        assertFalse(SystemVulkanDriver.softwareSessionGl("Google", "other", legacy))
        assertFalse(SystemVulkanDriver.softwareSessionGl("other", "kodiak", legacy))
        assertFalse(SystemVulkanDriver.softwareSessionGl("Google", "kodiak", "/vendor/lib64/hw/vulkan.samsung.so"))
        assertFalse(SystemVulkanDriver.softwareSessionGl("Google", "kodiak", null))
    }

    @Test fun platformNamedDriverRequiresTheActualImaginationVendor() {
        val driver = "/vendor/lib64/hw/vulkan.platform_gpu.so"
        assertEquals(driver, select(setOf(driver), hardware = "platform_gpu", vendor = { "0x1010" }))
        assertNull(select(setOf(driver), hardware = "platform_gpu", vendor = { "0x13b5" }))
        assertNull(select(setOf(driver), hardware = "platform_gpu", vendor = { null }))
        assertNull(select(setOf(driver), hardware = "platform_gpu", vendor = { "PowerVR" }))
    }

    @Test fun boardPlatformIsUsedWhenTheHardwareModuleIsAbsent() {
        val driver = "/vendor/lib64/hw/vulkan.board_gpu.so"
        assertEquals(driver, select(setOf(driver), hardware = "missing", platform = "board_gpu", vendor = { "4112" }))
    }

    @Test fun selectedApexTakesPrecedenceAndUsesTheMatchingDependencyDirectories() {
        val driver = "/apex/com.vendor.graphics/lib64/hw/vulkan.powervr.so"
        assertEquals(driver, select(setOf(legacy, driver), hardware = "powervr", apex = "com.vendor.graphics"))
        assertEquals(
            "/system/lib64:/system_ext/lib64:/apex/com.vendor.graphics/lib64:/apex/com.vendor.graphics/lib64/hw:/vendor/lib64/egl:/vendor/lib64/hw:/vendor/lib64",
            SystemVulkanDriver.librarySearchPath(driver),
        )
        assertEquals(legacy, select(setOf(legacy), hardware = "powervr", apex = "com.vendor.graphics"))
    }

    @Test fun apexSearchesItsLibraryRootBeforeHwWithoutChangingModulePriority() {
        val rootDriver = "/apex/com.vendor.graphics/lib64/vulkan.powervr.so"
        val hwDriver = "/apex/com.vendor.graphics/lib64/hw/vulkan.powervr.so"
        val boardDriver = "/apex/com.vendor.graphics/lib64/vulkan.board_gpu.so"
        assertEquals(rootDriver, select(setOf(rootDriver, hwDriver), hardware = "powervr", apex = "com.vendor.graphics"))
        assertEquals(hwDriver, select(setOf(boardDriver, hwDriver), hardware = "powervr", platform = "board_gpu", apex = "com.vendor.graphics"))
        assertEquals(SystemVulkanDriver.librarySearchPath(hwDriver), SystemVulkanDriver.librarySearchPath(rootDriver))
    }

    @Test fun existingUnrelatedSelectedDriverCannotFallBackToAStalePowerVrFile() {
        val driver = "/vendor/lib64/hw/vulkan.adreno.so"
        assertNull(select(setOf(driver, legacy), hardware = "adreno", vendor = { "0x5143" }))
    }

    @Test fun samsungMaliHalCannotEnableTheXclipseBridge() {
        val driver = "/vendor/lib64/hw/vulkan.samsung.so"
        assertNull(select(setOf(driver), hardware = "samsung", mali = true))
        assertNull(select(setOf(driver), mali = true))
        assertEquals(driver, select(setOf(driver), hardware = "samsung"))
    }

    @Test fun propertiesCannotEscapeTrustedDriverDirectories() {
        val checked = mutableListOf<String>()
        assertNull(SystemVulkanDriver.findHal("../../tmp/driver", "x:y", "../untrusted", false,
            { checked += it; false }, { error("no candidate should query Vulkan") }))
        assertEquals(listOf(legacy, "/vendor/lib64/hw/vulkan.samsung.so"), checked)
    }

    @Test fun vendorIdentityDoesNotUseModelNamesOrPartialMatches() {
        assertTrue(SystemVulkanDriver.imaginationVendor("0x1010"))
        assertTrue(SystemVulkanDriver.imaginationVendor("4112"))
        for (value in listOf(null, "", "0x10101", "0x13b5", "Pixel 11 Pro XL", "PowerVR CXTP"))
            assertFalse(value, SystemVulkanDriver.imaginationVendor(value))
    }

    @Test fun legacyDependencyOrderRemainsUnchanged() {
        assertEquals(
            "/system/lib64:/system_ext/lib64:/vendor/lib64/egl:/vendor/lib64/hw:/vendor/lib64",
            SystemVulkanDriver.librarySearchPath(legacy),
        )
    }
}
