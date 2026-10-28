// SPDX-License-Identifier: GPL-2.0-or-later

package org.dolphinemu.dolphinemu.overlay

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.view.MotionEvent
import org.dolphinemu.dolphinemu.features.input.model.InputOverrider

/**
 * Touch gesture controls for a game that only uses the Wii Remote's d-pad, A, B, +, -, 1 and 2.
 *
 * The whole screen becomes one flat surface, so the on-screen overlay is not used at all:
 *
 * - A drag acts as the d-pad. The direction is applied while the finger is still down, as soon as
 *   the finger travels further than [SWIPE_THRESHOLD_DP] from wherever it landed, and it stays
 *   applied until the finger comes back within [SWIPE_RELEASE_DP] (hysteresis), changes direction,
 *   or is lifted. Nothing waits for MotionEvent.ACTION_UP.
 * - The left half of the screen is A, the right half is B. The press is not made on contact: it
 *   waits [TAP_PRESS_MS] and is dropped again if the finger starts moving first, so a drag out of
 *   the half only presses a direction. A finger that stays put presses A or B and keeps holding it
 *   until it is lifted, and a tap shorter than that window is still pressed when it is lifted.
 * - The four corners are 1 (top left), 2 (top right), - (bottom left) and + (bottom right). A
 *   corner wins over the half it sits in, and its press is immediate.
 *
 * Every pointer owns its own state, so fingers never interfere with each other. Two fingers bound
 * to the same control are reference counted, so lifting one does not release the control the other
 * is still holding. All hit testing is done in proportions of the view, so a rotation, a resize or
 * a differently shaped screen needs no special handling.
 */
class GestureControls(private val controllerIndex: Int) {
    /**
     * State of a single pointer. Controls are identified by the [InputOverrider.ControlId] values,
     * and -1 means this pointer is not pressing anything right now.
     */
    private class PointerState(val downX: Float, val downY: Float) {
        var control: Int = NO_CONTROL

        /** A or B the finger landed on, still waiting to be pressed. */
        var pendingControl: Int = NO_CONTROL

        /** The scheduled press for [pendingControl], so that it can be called off. */
        var pendingPress: Runnable? = null
    }

    private val pointers = HashMap<Int, PointerState>()

    /** How many pointers are currently holding each control. */
    private val holdCounts = HashMap<Int, Int>()

    /** True while controls have been registered with the input core and may need releasing. */
    private var registered = false

    private val handler = Handler(Looper.getMainLooper())

    val isActive: Boolean
        get() = pointers.isNotEmpty()

    /**
     * Feeds an event to the gesture layer.
     *
     * @return true if the event was consumed. Only events of the gesture layer are consumed, so
     * the caller can simply stop handling an event once this returns true.
     */
    fun onTouchEvent(event: MotionEvent, width: Int, height: Int, density: Float): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN -> {
                val index = event.actionIndex
                val pointerId = event.getPointerId(index)
                val x = event.getX(index)
                val y = event.getY(index)

                val state = PointerState(x, y)
                pointers[pointerId] = state

                val landing = hitTest(x, y, width, height)
                if (landing == WIIMOTE_A || landing == WIIMOTE_B)
                    armPress(pointerId, state, landing)
                else
                    apply(pointerId, landing)
            }

            MotionEvent.ACTION_MOVE -> {
                for (index in 0 until event.pointerCount) {
                    val pointerId = event.getPointerId(index)
                    val state = pointers[pointerId] ?: continue
                    val x = event.getX(index)
                    val y = event.getY(index)

                    // A finger that moves has started a drag, so the A or B it landed on is not
                    // something it meant to press after all.
                    if (state.pendingControl != NO_CONTROL && hasMoved(state, x, y, density))
                        cancelPress(state)

                    if (state.pendingControl != NO_CONTROL)
                        continue

                    apply(
                        pointerId,
                        resolveDirection(state, x, y, density, width, height)
                    )
                }
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_POINTER_UP -> {
                val pointerId = event.getPointerId(event.actionIndex)
                val state = pointers[pointerId]
                if (state != null) {
                    val tap = state.pendingControl
                    cancelPress(state)
                    apply(pointerId, NO_CONTROL)
                    pointers.remove(pointerId)

                    // Released inside the waiting window, which means a short tap. It still has to
                    // reach the game, and for long enough that the input core samples it.
                    if (tap != NO_CONTROL)
                        pressBriefly(tap)
                }
            }

            MotionEvent.ACTION_CANCEL -> releaseAll()
        }

        return true
    }

    /**
     * Releases everything this layer is holding, for example when emulation pauses, the screen
     * rotates or the overlay is rebuilt. Without this a control could stay pressed forever.
     */
    fun releaseAll() {
        for (state in pointers.values)
            cancelPress(state)

        for (pointerId in pointers.keys.toList())
            apply(pointerId, NO_CONTROL)

        pointers.clear()

        for (control in holdCounts.keys.toList())
            InputOverrider.clearControlState(controllerIndex, control)

        holdCounts.clear()

        if (registered) {
            InputOverrider.unregisterWii(controllerIndex)
            registered = false
        }
    }

    /**
     * The control a pointer asks for while it is moving: a d-pad direction once it has travelled
     * far enough, otherwise whatever it was already pressing (which is A or B after a plain press,
     * or the direction it is still holding).
     */
    private fun resolveDirection(
        state: PointerState,
        x: Float,
        y: Float,
        density: Float,
        width: Int,
        height: Int
    ): Int {
        val dx = x - state.downX
        val dy = y - state.downY

        val threshold = SWIPE_THRESHOLD_DP * density
        val release = SWIPE_RELEASE_DP * density

        val distance = if (dx == 0f && dy == 0f) 0f else kotlin.math.hypot(dx, dy)
        val isHoldingDirection = state.control == WIIMOTE_UP || state.control == WIIMOTE_DOWN ||
                state.control == WIIMOTE_LEFT || state.control == WIIMOTE_RIGHT

        // A direction is only kept while the finger stays out past the release distance, so a
        // finger hovering on the threshold does not flicker between the d-pad and A/B. Coming back
        // in then presses whatever the finger originally landed on again, which is what makes a
        // d-pad press cancellation return control to A or B.
        if (distance < if (isHoldingDirection) release else threshold) {
            return if (isHoldingDirection)
                hitTest(state.downX, state.downY, width, height)
            else
                state.control
        }

        if (Math.abs(dx) >= Math.abs(dy))
            return if (dx > 0f) WIIMOTE_RIGHT else WIIMOTE_LEFT

        return if (dy > 0f) WIIMOTE_DOWN else WIIMOTE_UP
    }

    /** True once the finger is far enough from where it landed to count as a drag. */
    private fun hasMoved(state: PointerState, x: Float, y: Float, density: Float): Boolean =
        kotlin.math.hypot(x - state.downX, y - state.downY) >= TAP_CANCEL_DP * density

    /** Where a freshly pressed finger landed: a corner first, then the half it is in. */
    private fun hitTest(x: Float, y: Float, width: Int, height: Int): Int {
        if (width <= 0 || height <= 0)
            return WIIMOTE_A

        val cornerWidth = width * CORNER_WIDTH_RATIO
        val cornerHeight = height * CORNER_HEIGHT_RATIO
        val inCornerRow = y < cornerHeight || y > height - cornerHeight

        val isLeft = x < width / 2f
        val isTop = y < height / 2f

        if (inCornerRow && x < cornerWidth)
            return if (isTop) WIIMOTE_ONE else WIIMOTE_MINUS
        if (inCornerRow && x > width - cornerWidth)
            return if (isTop) WIIMOTE_TWO else WIIMOTE_PLUS

        return if (isLeft) WIIMOTE_A else WIIMOTE_B
    }

    /** Schedules the press of A or B, so that a drag that starts first can call it off. */
    private fun armPress(pointerId: Int, state: PointerState, control: Int) {
        state.pendingControl = control

        val press = Runnable { pressPending(pointerId, state, control) }
        state.pendingPress = press
        handler.postDelayed(press, TAP_PRESS_MS)
    }

    private fun pressPending(pointerId: Int, state: PointerState, control: Int) {
        if (pointers[pointerId] !== state || state.pendingControl != control)
            return

        state.pendingControl = NO_CONTROL
        state.pendingPress = null
        apply(pointerId, control)
    }

    private fun cancelPress(state: PointerState) {
        state.pendingPress?.let { handler.removeCallbacks(it) }
        state.pendingPress = null
        state.pendingControl = NO_CONTROL
    }

    /**
     * Presses [control] for a moment, for a tap that was over before the scheduled press was due.
     * A press and a release in the same frame would never be sampled by the input core.
     */
    private fun pressBriefly(control: Int) {
        hold(control)
        handler.postDelayed({ release(control) }, TAP_MIN_PRESS_MS)
    }

    /**
     * Moves one pointer to [control], keeping the reference counts in sync and pushing the net
     * change to the input core.
     */
    private fun apply(pointerId: Int, control: Int) {
        val state = pointers[pointerId] ?: return
        val previous = state.control

        if (previous == control)
            return

        state.control = control

        if (previous != NO_CONTROL)
            release(previous)

        if (control != NO_CONTROL)
            hold(control)
    }

    private fun hold(control: Int) {
        val count = (holdCounts[control] ?: 0) + 1
        holdCounts[control] = count

        if (count == 1) {
            register()
            InputOverrider.setControlState(controllerIndex, control, 1.0)
        }
    }

    private fun release(control: Int) {
        val count = (holdCounts[control] ?: 1) - 1

        if (count > 0) {
            holdCounts[control] = count
            return
        }

        holdCounts.remove(control)
        InputOverrider.clearControlState(controllerIndex, control)
    }

    /**
     * The input core only reads the overridden states once this controller has an override
     * function installed, which is what registering does. It is done on the first press and undone
     * in [releaseAll].
     */
    private fun register() {
        if (registered)
            return

        InputOverrider.registerWii(controllerIndex)
        registered = true
    }

    companion object {
        private const val NO_CONTROL = -1

        private const val WIIMOTE_A = InputOverrider.ControlId.WIIMOTE_A_BUTTON
        private const val WIIMOTE_B = InputOverrider.ControlId.WIIMOTE_B_BUTTON
        private const val WIIMOTE_ONE = InputOverrider.ControlId.WIIMOTE_ONE_BUTTON
        private const val WIIMOTE_TWO = InputOverrider.ControlId.WIIMOTE_TWO_BUTTON
        private const val WIIMOTE_PLUS = InputOverrider.ControlId.WIIMOTE_PLUS_BUTTON
        private const val WIIMOTE_MINUS = InputOverrider.ControlId.WIIMOTE_MINUS_BUTTON
        private const val WIIMOTE_UP = InputOverrider.ControlId.WIIMOTE_DPAD_UP
        private const val WIIMOTE_DOWN = InputOverrider.ControlId.WIIMOTE_DPAD_DOWN
        private const val WIIMOTE_LEFT = InputOverrider.ControlId.WIIMOTE_DPAD_LEFT
        private const val WIIMOTE_RIGHT = InputOverrider.ControlId.WIIMOTE_DPAD_RIGHT

        /** Corner width, as a fraction of the screen, that presses 1, 2, - and +. */
        private const val CORNER_WIDTH_RATIO = 0.28f

        /** How tall a corner reaches in from the top or bottom edge. */
        private const val CORNER_HEIGHT_RATIO = 0.36f

        /** How far a finger travels before the drag becomes a d-pad press. */
        private const val SWIPE_THRESHOLD_DP = 20f

        /** How far back it has to come before the d-pad press is dropped. */
        private const val SWIPE_RELEASE_DP = 12f

        /** How long A or B waits for the finger to prove it is not dragging. */
        private const val TAP_PRESS_MS = 40L

        /** How far the finger may drift in that time and still count as a press of A or B. */
        private const val TAP_CANCEL_DP = 8f

        /** How long a short tap is held for, so that the input core sees it at all. */
        private const val TAP_MIN_PRESS_MS = 60L

        /** Whether the user has turned the gesture layer on. */
        fun isEnabled(context: Context): Boolean =
            android.preference.PreferenceManager.getDefaultSharedPreferences(context)
                .getBoolean(PREF_ENABLED, false)

        fun setEnabled(context: Context, enabled: Boolean) {
            android.preference.PreferenceManager.getDefaultSharedPreferences(context)
                .edit().putBoolean(PREF_ENABLED, enabled).apply()
        }

        const val PREF_ENABLED = "GestureControlsEnabled"
    }
}
