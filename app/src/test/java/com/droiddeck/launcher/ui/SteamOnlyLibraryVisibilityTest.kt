package com.droiddeck.launcher.ui

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import com.droiddeck.launcher.BuildConfig
import com.droiddeck.launcher.R
import com.droiddeck.launcher.frontend.Library
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w1200dp-h752dp-land", application = android.app.Application::class)
class SteamOnlyLibraryVisibilityTest {
    @get:Rule val compose = createRobolectricComposeRule()

    @Before fun prepare() {
        assumeTrue(BuildConfig.STEAM_ONLY)
        LibraryView.source = "gog"
        LibraryView.query = ""
    }

    @After fun clear() {
        LibraryView.source = LIBRARY_ALL
        LibraryView.query = ""
        Steps.drop()
    }

    @Test fun aStaleStoreSelectionCannotDisplayAStoreGameOrRestoreImportTools() {
        val state = FrontEndState(
            installed = "r9", ready = true, available = null, busy = false, stage = "", percent = -1,
            desktopInstalled = true, offlineAccount = null, offline = false, frameGenLabel = "",
            romsDir = null, logsEnabled = false, emulators = emptyList(), running = null, gameStoresEnabled = true,
            steamGames = listOf(
                Library.SteamGame(440, "Steam game", null, "internal"),
                Library.SteamGame(1, "Old GOG game", null, Library.ADDED, source = "gog", storeId = "1"),
                Library.SteamGame(2, "Local import", null, Library.ADDED, source = Library.ADDED),
            ),
        )
        assertFalse("stores" in navigationKeys(state))
        assertTrue(navigationKeys(state).containsAll(listOf("steam", "games", "desktop", "components")))
        compose.mainClock.autoAdvance = false
        compose.setContent {
            DroidDeckTheme {
                CompositionLocalProvider(LocalFrontFocus provides FrontFocus()) {
                    FocusGlideHost {
                        LibraryPage(state, actions(), "app:1", {}, Modifier.fillMaxSize())
                    }
                }
            }
        }
        compose.mainClock.advanceTimeBy(2_000)
        compose.onNodeWithText("Steam game").assertExists()
        compose.onNodeWithText("Old GOG game").assertDoesNotExist()
        compose.onNodeWithText("Local import").assertDoesNotExist()
        compose.onNodeWithContentDescription(RuntimeEnvironment.getApplication().getString(R.string.games_add)).assertDoesNotExist()
    }

    private fun actions() = FrontEndActions(
        onPlay = {}, onPlayDesktopUi = {}, onSteamGame = {}, onDesktop = {}, onEmulator = {},
        onRom = {}, onResume = {}, onSteamSettings = {}, onDesktopSettings = {},
        onInstallPackage = {}, onRemovePackage = {}, onRuntime = {}, onFrameGenPick = {},
        onImportLossless = {}, onProtons = {}, onComponents = {}, onPerformance = {},
        onRoms = {}, onFiles = {}, onLogs = {}, onOffline = {},
        onWirelessAdbPair = { _, _, _, _ -> }, onWirelessAdbApply = { _, _, _, _ -> },
        onSetPhantomProcessLimit = { _, _ -> },
    )
}
