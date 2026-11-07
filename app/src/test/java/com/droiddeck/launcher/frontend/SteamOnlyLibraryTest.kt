package com.droiddeck.launcher.frontend

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SteamOnlyLibraryTest {
    @Test fun keepsSteamGamesAndSteamManagedShortcuts() {
        assertTrue(Library.allowedInBuild(Library.SteamGame(440, "Steam game", null, "internal"), true))
        assertTrue(Library.allowedInBuild(Library.SteamGame(-1, "Steam shortcut", null, "internal", gameId = -1L), true))
        assertTrue(Library.allowedInBuild(Library.SteamGame(250820, "SteamVR", null, "internal"), true))
    }

    @Test fun excludesImportedGamesAndStoreRowsEvenWithSteamLikeMetadata() {
        val excluded = listOf(
            Library.SteamGame(440, "Imported Steam mapping", null, Library.ADDED, source = Library.ADDED),
            Library.SteamGame(440, "Old imported row", null, Library.ADDED),
            Library.SteamGame(1, "Store game", null, "internal", source = "gog", storeId = "1"),
            Library.SteamGame(440, "Store id", null, "internal", storeId = "440"),
            Library.SteamGame(1, "Cloud game", null, Library.XBOX_CLOUD),
            Library.SteamGame(1, "Epic launcher", null, Library.EPIC_LAUNCHER),
            Library.SteamGame(1, "Battle.net launcher", null, Library.BATTLENET_LAUNCHER),
        )
        excluded.forEach { game ->
            assertFalse(game.name, Library.allowedInBuild(game, true))
            assertTrue(game.name, Library.allowedInBuild(game, false))
        }
    }
}
