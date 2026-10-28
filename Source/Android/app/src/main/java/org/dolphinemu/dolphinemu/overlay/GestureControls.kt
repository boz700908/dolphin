// SPDX-License-Identifier: GPL-2.0-or-later

package org.dolphinemu.dolphinemu.overlay

import android.content.Context
import android.view.MotionEvent
import org.dolphinemu.dolphinemu.assist.AssistTts
import org.dolphinemu.dolphinemu.features.input.model.InputOverrider

/**
 * Touch gesture controls for a game that only uses the Wii Remote's d-pad, A, B, +, -, 1 and 2.
 *
 * The whole screen becomes one flat surface, so the on-screen overlay is not used at all. It is
 * split into three vertical zones:
 *
 * - The middle of the screen, from [SIDE_RATIO] to one minus that, is the d-pad and nothing else.
 *   A drag applies a direction while the finger is still down, as soon as it travels further than
 *   [SWIPE_THRESHOLD_DP] from wherever it landed, and keeps it applied until the finger comes back
 *   within [SWIPE_RELEASE_DP] (hysteresis), changes direction, or is lifted. Touching here presses
 *   no button, so a drag can never confirm anything first. Nothing waits for ACTION_UP.
 * - The left band is A and the right band is B, pressed the moment a finger lands in it, so a held
 *   A or B is a held finger. A drag started here still becomes a d-pad direction once it travels
 *   far enough, but the middle is the place to drag from.
 * - The four corners of the bands are 1 (top left), 2 (top right), - (bottom left) and + (bottom
 *   right). A corner wins over the band it sits in and is pressed immediately.
 *
 * Every pointer owns its own state, so fingers never interfere with each other. Two fingers bound
 * to the same control are reference counted, so lifting one does not release the control the other
 * is still holding. All hit testing is done in proportions of the view, so a rotation, a resize or
 * a differently shaped screen needs no special handling.
 */
class GestureControls(private val controllerIndex: Int) {
    /**
     * State of a single pointer. Controls are identified by the [InputOverrider.ControlId] values,
     * and [NO_CONTROL] means this pointer is not pressing anything right now.
     */
    private class PointerState(val downX: Float, val downY: Float) {
        var control: Int = NO_CONTROL
    }

    private val pointers = HashMap<Int, PointerState>()

    /** How many pointers are currently holding each control. */
    private val holdCounts = HashMap<Int, Int>()

    /** True while controls have been registered with the input core and may need releasing. */
    private var registered = false

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

                pointers[pointerId] = PointerState(x, y)
                apply(pointerId, hitTest(x, y, width, height))
            }

            MotionEvent.ACTION_MOVE -> {
                for (index in 0 until event.pointerCount) {
                    val pointerId = event.getPointerId(index)
                    val state = pointers[pointerId] ?: continue

                    apply(
                        pointerId,
                        resolveDirection(
                            state, event.getX(index), event.getY(index),
                            density, width, height
                        )
                    )
                }
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_POINTER_UP -> {
                val pointerId = event.getPointerId(event.actionIndex)
                apply(pointerId, NO_CONTROL)
                pointers.remove(pointerId)
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
        for (pointerId in pointers.keys.toList())
            apply(pointerId, NO_CONTROL)

        pointers.clear()

        for (control in holdCounts.keys.toList())
            InputOverrider.clearControlState(controllerIndex, control)

        holdCounts.clear()
        AssistTts.noteAllInputsReleased()

        if (registered) {
            InputOverrider.unregisterWii(controllerIndex)
            registered = false
        }
    }

    /**
     * The control a pointer asks for while it is moving: a d-pad direction once it has travelled
     * far enough, otherwise whatever it was already pressing (which is A or B after a plain press,
     * the direction it is still holding, or nothing after a drag out of the middle).
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
        // d-pad press cancellation return control to the zone the drag started in.
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

    /**
     * What a freshly pressed finger landed on: a corner first, then A or B in the side bands, and
     * nothing at all in the middle, which is the drag area.
     */
    private fun hitTest(x: Float, y: Float, width: Int, height: Int): Int {
        if (width <= 0 || height <= 0)
            return NO_CONTROL

        val sideWidth = width * SIDE_RATIO
        val cornerHeight = height * CORNER_HEIGHT_RATIO
        val inCornerRow = y < cornerHeight || y > height - cornerHeight

        val isTop = y < height / 2f

        if (inCornerRow && x < sideWidth)
            return if (isTop) WIIMOTE_ONE else WIIMOTE_MINUS
        if (inCornerRow && x > width - sideWidth)
            return if (isTop) WIIMOTE_TWO else WIIMOTE_PLUS

        if (x < sideWidth)
            return WIIMOTE_A
        if (x > width - sideWidth)
            return WIIMOTE_B

        return NO_CONTROL
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

        // The narration tells a press from idle narration by these events alone, so every press is
        // reported, whichever control it was.
        AssistTts.noteInputDown()
    }

    private fun release(control: Int) {
        val count = (holdCounts[control] ?: 1) - 1

        if (count > 0) {
            holdCounts[control] = count
            return
        }

        holdCounts.remove(control)
        InputOverrider.clearControlState(controllerIndex, control)
        AssistTts.noteInputUp()
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
        /** No control: a pointer dragging in the middle of the screen presses nothing yet. */
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

        /** Width of the A and B bands at each side, as a fraction of the screen. */
        private const val SIDE_RATIO = 0.30f

        /** How tall a corner reaches in from the top or bottom edge. */
        private const val CORNER_HEIGHT_RATIO = 0.32f

        /** How far a finger travels before the drag becomes a d-pad press. */
        private const val SWIPE_THRESHOLD_DP = 20f

        /** How far back it has to come before the d-pad press is dropped. */
        private const val SWIPE_RELEASE_DP = 12f

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
