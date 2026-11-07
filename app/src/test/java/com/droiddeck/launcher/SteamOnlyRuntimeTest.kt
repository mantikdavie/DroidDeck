package com.droiddeck.launcher

import android.app.Application
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import com.droiddeck.launcher.core.AddonPack
import com.droiddeck.launcher.session.SessionPrefs
import com.droiddeck.launcher.stores.CatalogItem
import com.droiddeck.launcher.stores.Store
import com.droiddeck.launcher.stores.StoreLaunchRequests
import com.droiddeck.launcher.stores.StoresState
import com.droiddeck.launcher.stores.download.DownloadEntry
import com.droiddeck.launcher.stores.download.DownloadQueue
import com.droiddeck.launcher.stores.download.DownloadState
import com.droiddeck.launcher.stores.download.SavedQueue
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import java.io.File
import java.util.concurrent.atomic.AtomicInteger
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class SteamOnlyRuntimeTest {
    private val app get() = RuntimeEnvironment.getApplication()

    @Before fun steamOnlyBuild() {
        assumeTrue(BuildConfig.STEAM_ONLY)
    }

    @Test fun storesStayDisabledWithoutOverwritingTheSavedPreference() {
        val preferences = SessionPrefs.sessionPrefs(app)
        preferences.edit().putBoolean("gameStoresEnabled", true).commit()
        assertFalse(SessionPrefs.gameStoresEnabled(app))
        SessionPrefs.setGameStoresEnabled(app, false)
        assertTrue(preferences.getBoolean("gameStoresEnabled", false))
        SessionPrefs.setGameStoresEnabled(app, true)
        assertFalse(SessionPrefs.gameStoresEnabled(app))
        assertTrue(preferences.getBoolean("gameStoresEnabled", false))
    }

    @Test fun anOldDownloadQueueIsPreservedAndNeverRestored() {
        val file = File(app.filesDir, "store-queue.json")
        val saved = """[{"paused":false,"root":"/old-games","item":{"store":"gog","id":"steam-only-saved","title":"Old game","owned":true}}]"""
        file.writeText(saved)
        val item = CatalogItem(Store.GOG, "steam-only-new", "New game", imageUrl = null)
        SavedQueue.remember(app, item, app.filesDir)
        SavedQueue.save(listOf(DownloadEntry(item.store, item.id, item.title, state = DownloadState.QUEUED)))
        SavedQueue.restore(app)
        StoresState.refresh(app)
        org.robolectric.shadows.ShadowLooper.idleMainLooper()
        assertEquals(saved, file.readText())
        assertFalse(File(app.filesDir, "store-queue.json.tmp").exists())
        assertNull(DownloadQueue.entry("gog:steam-only-saved"))
        assertNull(DownloadQueue.entry(item.key))
    }

    @Test fun downloadsCannotCreateAJobOrRestoreARetry() {
        val entry = DownloadEntry(Store.EPIC, "steam-only-queue", "Game")
        val calls = AtomicInteger()
        DownloadQueue.enqueue(app, entry) {
            calls.incrementAndGet()
            error("A Steam-only build cannot create a store download")
        }
        DownloadQueue.recordFailed(app, entry, "old failure") { calls.incrementAndGet() }
        DownloadQueue.resume(app, entry.key)
        DownloadQueue.retry(app, entry.key)
        assertEquals(0, calls.get())
        assertNull(DownloadQueue.entry(entry.key))
        assertNull(shadowOf(app).nextStartedService)
    }

    @Test fun sessionStoreRequestsCannotStartStoreIntegrations() {
        StoreLaunchRequests.lastLaunched = null
        val operations = listOf("xbox-released", "xbox-renew", "xbox-prepare", "gog-achievements", "epic-code",
            "cloud-down", "cloud-up", "epic-sign-in", "epic-play-in-steam", "steam-run", "epic-signed-out", "epic-client-handoff")
        operations.forEach { operation ->
            val reply = StoreLaunchRequests.dispatch(app, JSONObject().put("op", operation).put("store", "gog")
                .put("id", "440").put("url", "https://example.com/sign-in"))
            assertFalse(operation, reply.getBoolean("ok"))
            assertEquals(operation, "steam-only", reply.getString("reason"))
        }
        assertNull(StoreLaunchRequests.lastLaunched)
        assertNull(shadowOf(app).nextStartedActivity)
    }

    @Test fun steamAndDesktopCanStillOpenWebLinksButNotApplicationSchemes() {
        val url = "https://store.steampowered.com/account/"
        val reply = StoreLaunchRequests.dispatch(app, JSONObject().put("op", "open-url").put("url", url))
        assertTrue(reply.toString(), reply.getBoolean("ok"))
        val opened = shadowOf(app).nextStartedActivity
        assertEquals(Intent.ACTION_VIEW, opened.action)
        assertEquals(url, opened.dataString)
        assertTrue(opened.flags and Intent.FLAG_ACTIVITY_NEW_TASK != 0)
        listOf("file:///sdcard/game.exe", "intent://game", "javascript:alert(1)", "steam://rungameid/440").forEach { invalid ->
            val rejected = StoreLaunchRequests.dispatch(app, JSONObject().put("op", "open-url").put("url", invalid))
            assertFalse(invalid, rejected.getBoolean("ok"))
            assertEquals("not-http", rejected.getString("reason"))
        }
        assertNull(shadowOf(app).nextStartedActivity)
    }

    @Test fun excludedPacksIgnoreCompleteDeviceCopiesAndLeaveTheirFilesAlone() {
        for (name in listOf("xbox", "comet", "butler")) {
            val version = "a".repeat(64)
            val root = File(app.filesDir, "addons/$name")
            val cache = File(root, version).apply { mkdirs() }
            val tool = File(cache, "tool").apply { writeText("existing pack data") }
            val complete = File(cache, AddonPack.COMPLETE).apply { writeText(version) }
            val bundleCalls = AtomicInteger()
            val readyCalls = AtomicInteger()
            for (reuse in listOf(false, true)) {
                val pack = AddonPack(name, if (reuse) "" else version, 1L, listOf("tool"), reuseDeviceCopy = reuse,
                    bundle = { bundleCalls.incrementAndGet(); error("Excluded pack bundle opened") })
                assertFalse(name, pack.available)
                assertFalse(name, pack.installed(app))
                assertEquals(AddonPack.State.Missing, pack.refresh(app))
                pack.ensure(app) { readyCalls.incrementAndGet() }
                assertEquals(AddonPack.State.Failed, pack.state.value)
                assertFalse(name, pack.await(app))
            }
            assertEquals(0, bundleCalls.get())
            assertEquals(0, readyCalls.get())
            assertEquals("existing pack data", tool.readText())
            assertEquals(version, complete.readText())
            assertEquals(listOf(version), root.list().orEmpty().toList())
        }
    }

    @Suppress("DEPRECATION")
    @Test fun allStoreComponentsAreDisabledAndSteamEntryPointsRemainEnabled() {
        val flags = PackageManager.GET_ACTIVITIES or PackageManager.GET_SERVICES or PackageManager.MATCH_DISABLED_COMPONENTS
        val info = app.packageManager.getPackageInfo(app.packageName, flags)
        val components: List<android.content.pm.ComponentInfo> = info.activities.orEmpty().toList() + info.services.orEmpty().toList()
        val stores = components.filter { it.name.startsWith("com.droiddeck.launcher.stores.") }
        assertEquals(setOf(
            "com.droiddeck.launcher.stores.StoreLoginActivity",
            "com.droiddeck.launcher.stores.download.StoreDownloadService",
            "com.droiddeck.launcher.stores.xbox.XboxLoginActivity",
            "com.droiddeck.launcher.stores.xbox.XboxCloudPlayerActivity",
            "com.droiddeck.launcher.stores.xbox.XboxCloudTokenService",
            "com.droiddeck.launcher.stores.xbox.XboxNativePlayerActivity",
            "com.droiddeck.launcher.stores.xbox.XboxCloudActivity",
            "com.droiddeck.launcher.stores.xbox.XboxCloudSettingsActivity",
        ), stores.map { it.name }.toSet())
        stores.forEach { assertFalse(it.name, it.enabled) }
        for (name in listOf("FrontEndActivity", "SessionActivity")) {
            assertTrue(app.packageManager.getActivityInfo(ComponentName(app, "com.droiddeck.launcher.$name"), 0).enabled)
        }
    }
}
