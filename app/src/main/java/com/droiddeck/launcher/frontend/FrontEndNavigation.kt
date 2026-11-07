package com.droiddeck.launcher.frontend

import com.droiddeck.launcher.BuildConfig

object FrontEndNavigation {
    private val pages = setOf("steam", "games", "desktop", "components", "android-apps", "setup", "updates",
        "setup:launcher", "setup:integration", "setup:sync")

    fun destination(target: String, steamOnly: Boolean = BuildConfig.STEAM_ONLY): String = when {
        !steamOnly || target in pages -> target
        target.startsWith("app:") && target.removePrefix("app:").toIntOrNull() != null -> target
        else -> "games"
    }
}
