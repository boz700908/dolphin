// Copyright 2026 Dolphin Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
package org.dolphinemu.dolphinemu.assist

/**
 * Raw guest RAM access for the narration runtime.
 *
 * The Python narration code runs in this same process, so it reads MEM1/MEM2
 * through the host pointers below instead of the emulator copying 88 MiB per
 * frame. Every accessor returns 0 when no game is running, which is the only
 * state check callers need.
 *
 * These addresses belong to Dolphin's own mapping and are invalidated when the
 * core stops. Callers must re-read them after every start, and must never write.
 */
object AssistNative {
    /**
     * Host address of MEM1, or 0 when emulation is not running.
     */
    external fun GetMem1Address(): Long

    /**
     * Host address of MEM2, or 0 when emulation is not running.
     */
    external fun GetMem2Address(): Long

    /**
     * Usable byte count of MEM1, or 0 when emulation is not running.
     */
    external fun GetMem1Size(): Long

    /**
     * Usable byte count of MEM2, or 0 when emulation is not running.
     */
    external fun GetMem2Size(): Long
}
