// Copyright 2026 Dolphin Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
package org.dolphinemu.dolphinemu.assist

import android.content.Context

/**
 * Holds the user's screen reader preference.
 *
 * This is the master switch: [AssistRuntime] reads it when emulation resumes,
 * the in-game menu hides its reader commands while it is off, and the settings
 * screen writes it. It lives in its own preference file under the key it has
 * always used, so an existing setting survives.
 */
object AssistSpeech {
    private const val PREF_FILE = "assist"
    private const val PREF_ENABLED = "AssistSpeechEnabled"

    fun isEnabled(context: Context): Boolean =
        context.applicationContext
            .getSharedPreferences(PREF_FILE, Context.MODE_PRIVATE)
            .getBoolean(PREF_ENABLED, false)

    fun setEnabled(context: Context, enabled: Boolean) {
        context.applicationContext
            .getSharedPreferences(PREF_FILE, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(PREF_ENABLED, enabled)
            .apply()
    }
}
