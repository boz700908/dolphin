// Copyright 2026 Dolphin Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
package org.dolphinemu.dolphinemu.assist

import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Context
import android.view.accessibility.AccessibilityManager
import org.dolphinemu.dolphinemu.overlay.GestureControls

/**
 * Turns the reader and the gesture layer on for someone who is already using a
 * screen reader.
 *
 * A player who has a screen reader running cannot use the game with the
 * on-screen pads: the reader takes every touch for itself. So when the app
 * starts and a reader is found, [adopt] switches the spoken interface and the
 * gesture layer on, and the game is usable without touching a setting first.
 *
 * It only ever turns things on. A reader that is switched off again, or a
 * preference the player turned off on purpose, is left alone: nothing here
 * writes `false`.
 */
object ScreenReader {
    /**
     * Whether Android reports a screen reader that is switched on and in use.
     *
     * Touch exploration is what the reader uses to reach the screen, and it is
     * the part that breaks the pads, so it counts on its own. A spoken feedback
     * service counts as well, for readers that speak without exploring.
     */
    fun isActive(context: Context): Boolean {
        val manager = context.applicationContext
            .getSystemService(Context.ACCESSIBILITY_SERVICE) as? AccessibilityManager
            ?: return false

        if (!manager.isEnabled) return false
        if (manager.isTouchExplorationEnabled) return true

        return manager
            .getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_SPOKEN)
            .isNotEmpty()
    }

    /** Switches the reader and the gesture layer on when a screen reader is in use. */
    fun adopt(context: Context) {
        if (!isActive(context)) return

        if (!AssistSpeech.isEnabled(context)) AssistSpeech.setEnabled(context, true)
        if (!GestureControls.isEnabled(context)) GestureControls.setEnabled(context, true)
    }
}
