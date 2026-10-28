// Copyright 2026 Dolphin Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
package org.dolphinemu.dolphinemu.assist

import android.content.Context
import android.media.AudioAttributes
import android.media.SoundPool
import android.util.Log

/**
 * Plays the short kungfu cue WAVs next to the game audio.
 *
 * Each cue file already carries its player in the correct channel (P1 left,
 * P2 right), so no panning or mixing is done here: one cue is one file. The
 * previous cue is stopped before the next starts, because only the most recent
 * round state is meaningful.
 *
 * Loading is asynchronous and a cue is silent until its sample is ready; a cue
 * requested before then is dropped rather than delayed.
 */
object AssistAudio {
    private const val TAG = "AssistAudio"

    // Enough for the four cue files at once.
    private const val MAX_STREAMS = 4

    private var soundPool: SoundPool? = null

    // Cue name ("near:1") to SoundPool sample id, filled in as loading finishes.
    private val sampleIds = HashMap<String, Int>()

    // Sample ids pending load, so results can be matched back to a cue name.
    private val loadingIds = HashMap<Int, String>()

    private var currentStream = 0

    /**
     * Loads every cue in [files], keyed by cue name and pointing at a wav path.
     *
     * Must be called from the main thread. Loading completes in the background;
     * see [play] for how a request that arrives early behaves.
     */
    @JvmStatic
    fun prepare(context: Context, files: Map<String, String>) {
        if (soundPool != null) {
            return
        }

        val pool = SoundPool.Builder()
            .setMaxStreams(MAX_STREAMS)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_GAME)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build()
            )
            .build()

        pool.setOnLoadCompleteListener { _, sampleId, status ->
            val name = loadingIds.remove(sampleId)
            if (status == 0 && name != null) {
                synchronized(sampleIds) { sampleIds[name] = sampleId }
            } else if (name != null) {
                Log.w(TAG, "Sample load failed ($status): $name")
            }
        }

        soundPool = pool
        for ((name, path) in files) {
            val id = pool.load(path, 1)
            loadingIds[id] = name
        }
    }

    /**
     * Plays one cue, replacing whatever was playing.
     *
     * Called from the narration thread. A cue whose sample has not finished
     * loading is skipped: a late cue would describe a round state that has
     * already passed.
     */
    @JvmStatic
    fun play(name: String, loop: Boolean) {
        val pool = soundPool ?: return
        val sampleId = synchronized(sampleIds) { sampleIds[name] } ?: return

        if (currentStream != 0) {
            pool.stop(currentStream)
            currentStream = 0
        }

        val repeat = if (loop) -1 else 0
        currentStream = pool.play(sampleId, 1.0f, 1.0f, 1, repeat, 1.0f)
    }

    /**
     * Stops the cue that is playing, if any.
     */
    @JvmStatic
    fun stop() {
        val pool = soundPool ?: return
        if (currentStream != 0) {
            pool.stop(currentStream)
            currentStream = 0
        }
    }

    /**
     * Releases the pool. Called when the emulation activity finishes.
     */
    @JvmStatic
    fun shutdown() {
        stop()
        soundPool?.release()
        soundPool = null
        synchronized(sampleIds) { sampleIds.clear() }
        loadingIds.clear()
    }
}
