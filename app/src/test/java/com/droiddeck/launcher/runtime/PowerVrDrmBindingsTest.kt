package com.droiddeck.launcher.runtime

import java.io.File
import java.nio.file.Files
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class PowerVrDrmBindingsTest {
    @get:Rule val temp = TemporaryFolder()

    private fun bindings(base: File, node: String, numbers: String?, listable: Set<String>): List<String> {
        val command = mutableListOf<String>()
        LinuxRuntime.bindPowerVrGpuNode(base, command, node, numbers, { it in listable })
        return command.chunked(2).map { (option, spec) ->
            assertEquals("-b", option)
            spec
        }
    }

    @Test fun inaccessibleDrmClassIsExposedEvenWhenDeviceDirectoryIsListable() {
        val base = temp.newFolder()
        val binds = bindings(base, "/dev/dri/renderD130", "226:130", setOf("/dev/dri", "/sys/class/powercap"))

        assertEquals(listOf("${base.path}/class:/sys/class/drm"), binds)
        assertEquals(listOf("renderD130"), File(base, "class").list()!!.toList())
        assertEquals("/sys/dev/char/226:130", Files.readSymbolicLink(File(base, "class/renderD130").toPath()).toString())
        assertFalse(File(base, "dri").exists())
    }

    @Test fun deniedListingsKeepTheRealRenderNodeAndItsActualDeviceNumbers() {
        val base = temp.newFolder()
        val node = "/dev/dri/renderD200"
        val binds = bindings(base, node, "226:200", emptySet())

        assertEquals(listOf(
            "${base.path}/dri:/dev/dri", "$node:$node",
            "${base.path}/class:/sys/class/drm", "${base.path}/empty-powercap:/sys/class/powercap",
        ), binds)
        assertEquals(listOf("renderD200"), File(base, "dri").list()!!.toList())
        assertEquals("/sys/dev/char/226:200", Files.readSymbolicLink(File(base, "class/renderD200").toPath()).toString())
        assertTrue(binds.none { it.endsWith(":/sys/dev/char") })
        assertEquals(binds, bindings(base, node, "226:200", emptySet()))
    }

    @Test fun readableDeviceAndDrmDirectoriesAreKept() {
        val base = temp.newFolder()
        assertTrue(bindings(base, "/dev/dri/renderD128", "226:128", setOf("/dev/dri", "/sys/class/drm")).isEmpty())
        assertTrue(base.list()!!.isEmpty())
    }

    @Test fun missingDeviceNumbersNeverInventASysfsIdentity() {
        val base = temp.newFolder()
        assertTrue(bindings(base, "/dev/dri/renderD129", null, setOf("/dev/dri")).isEmpty())
        assertTrue(base.list()!!.isEmpty())
    }
}
