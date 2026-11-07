package com.droiddeck.launcher.frontend

import org.junit.Assert.assertEquals
import org.junit.Test

class FrontEndNavigationTest {
    @Test fun restoresUnsupportedDestinationsToSteamLibrary() {
        listOf("stores", "stores:downloads", "store", "app:gog:440", "app:xbox:cloud", "app:added:440",
            "emu:rpcs3", "rom:rpcs3:0", "user:script", "unknown").forEach {
            assertEquals(it, "games", FrontEndNavigation.destination(it, true))
            assertEquals(it, FrontEndNavigation.destination(it, false))
        }
    }

    @Test fun retainsSteamDesktopAndDeviceConfigurationRoutes() {
        listOf("steam", "games", "app:440", "app:-1", "app:250820", "desktop", "components", "setup",
            "setup:launcher", "setup:integration", "setup:sync", "updates", "android-apps").forEach {
            assertEquals(it, FrontEndNavigation.destination(it, true))
        }
    }
}
