// Copyright 2026 Dolphin Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
package org.dolphinemu.dolphinemu.assist

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import java.util.Locale

/**
 * Screen-reader speech output through the platform TextToSpeech engine.
 *
 * Narration runs on its own Python thread, which must never block on speech, so every request is
 * posted to the main looper rather than awaited.
 *
 * What interrupts what is decided by the player's own input and by nothing else. Pressing a control
 * on the screen, on a controller, on a keyboard or on a remote is an event; while at least one such
 * control is held down, the messages the game reports are the answer to that press and cut into
 * whatever is being read. With nothing held, messages queue up behind each other so that each one
 * can be read to its end. Nothing here counts time: a press and the release that ends it are what
 * the narration listens to, rather than a window guessed at after the press.
 *
 * Requests that arrive before the engine finishes initializing are held and spoken once, in order,
 * so the first announcement of a session is not lost.
 *
 * The engine is started lazily on the first request and released by shutdown().
 */
object AssistTts {
    private const val TAG = "AssistTts"

    /** How many pre-initialization messages are kept; the oldest go first. */
    private const val PENDING_LIMIT = 8

    private val mainHandler = Handler(Looper.getMainLooper())

    private var engine: TextToSpeech? = null

    // True once onInit has reported success; until then requests are queued.
    private var ready = false

    // True after initialization failed, so later requests are dropped instead
    // of accumulating for an engine that will never speak.
    private var failed = false

    private val pending = ArrayDeque<String>()

    // The text the engine is reading aloud right now. The game reports some states every frame, and
    // the same words arriving again while they are audible are not restarted.
    private var speakingText: String? = null

    // Controls the player currently holds down, from any device. Nothing counts time; this is the
    // whole of what decides an interruption.
    private var heldInputs = 0

    /**
     * Starts the engine now if it was not started yet.
     *
     * Safe to call repeatedly; the second call is a no-op. Must be called from
     * the main thread.
     */
    @JvmStatic
    fun prepare(context: Context) {
        if (engine != null || failed) {
            return
        }

        val appContext = context.applicationContext
        engine = TextToSpeech(appContext) { status ->
            // This callback arrives on the main thread already.
            if (status == TextToSpeech.SUCCESS) {
                onEngineReady()
            } else {
                Log.w(TAG, "TextToSpeech initialization failed: $status")
                failed = true
                pending.clear()
                releaseEngine()
            }
        }
    }

    /**
     * Speaks one already-localized message.
     *
     * Called from the narration thread.
     */
    @JvmStatic
    fun say(text: String) {
        if (text.isEmpty()) {
            return
        }

        mainHandler.post {
            if (failed) {
                Log.w(TAG, "Dropping speech after initialization failure: $text")
                return@post
            }

            if (text == speakingText || text == pending.lastOrNull()) {
                return@post
            }

            if (!ready) {
                if (pending.size >= PENDING_LIMIT) {
                    pending.removeFirst()
                }
                pending.addLast(text)
                return@post
            }

            // A message while the player holds a control is the answer to that press, so it cuts
            // in; a message nobody pressed anything for is narration that has to be allowed to
            // finish.
            speakNow(text, flush = heldInputs > 0)
        }
    }

    /**
     * Records that a control went down, whichever device it is on. Called from the touch layer, the
     * key dispatch of the emulation activity and the gesture layer, all of which run on the main
     * thread.
     */
    @JvmStatic
    fun noteInputDown() {
        heldInputs++
    }

    /**
     * Records that such a control went back up.
     */
    @JvmStatic
    fun noteInputUp() {
        if (heldInputs > 0) {
            heldInputs--
        }
    }

    /**
     * Forgets everything that was held, for when the input state is thrown away wholesale, such as
     * when the overlay is rebuilt or the game is paused.
     */
    @JvmStatic
    fun noteAllInputsReleased() {
        heldInputs = 0
    }

    /**
     * Drops speech that has not started yet.
     *
     * Used when the game exits or narration is paused, so stale cues are not
     * read out later. The utterance already in progress is left alone.
     */
    @JvmStatic
    fun clear() {
        mainHandler.post {
            pending.clear()
            engine?.stop()
        }
    }

    /**
     * Releases the engine. Called when the emulation activity finishes.
     */
    @JvmStatic
    fun shutdown() {
        mainHandler.post {
            pending.clear()
            ready = false
            releaseEngine()
        }
    }

    private fun onEngineReady() {
        ready = true

        // Japanese titles read correctly through the zh-CN mapping, so prefer a
        // locale the user actually has; otherwise keep the engine default.
        val tts = engine ?: return
        val preferred = tts.isLanguageAvailable(Locale.SIMPLIFIED_CHINESE)
        if (preferred == TextToSpeech.LANG_AVAILABLE ||
            preferred == TextToSpeech.LANG_COUNTRY_AVAILABLE ||
            preferred == TextToSpeech.LANG_COUNTRY_VAR_AVAILABLE
        ) {
            tts.language = Locale.SIMPLIFIED_CHINESE
        }

        // The utterance id is the text itself, so the engine can report back what it is reading
        // without a table to look it up in.
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {
                speakingText = utteranceId
            }

            override fun onDone(utteranceId: String?) {
                finish(utteranceId)
            }

            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) {
                Log.w(TAG, "Utterance failed: $utteranceId")
                finish(utteranceId)
            }

            override fun onStop(utteranceId: String?, interrupted: Boolean) {
                finish(utteranceId)
            }

            private fun finish(utteranceId: String?) {
                if (speakingText == utteranceId) {
                    speakingText = null
                }
            }
        })

        // Everything collected before the engine existed is spoken now. Only the
        // first one flushes: flushing the rest would leave just the last audible.
        while (pending.isNotEmpty()) {
            speakNow(pending.removeFirst(), flush = pending.isEmpty())
        }
    }

    private fun speakNow(text: String, flush: Boolean = true) {
        val tts = engine ?: return
        // QUEUE_FLUSH: say this now and drop whatever is still speaking, because a
        // message that is still being read describes a moment that has passed.
        val mode = if (flush) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD
        val result = tts.speak(text, mode, null, text)
        if (result != TextToSpeech.SUCCESS) {
            Log.w(TAG, "Speak rejected ($result): $text")
        }
    }

    private fun releaseEngine() {
        engine?.let {
            it.stop()
            it.shutdown()
        }
        engine = null
        speakingText = null
        heldInputs = 0
    }
}
