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
 * Narration runs on its own Python thread, which must never block on speech, so
 * every request is posted to the main looper rather than awaited.
 *
 * A new message interrupts whatever is being spoken. This game reports state
 * continuously, and queueing made the speech fall further and further behind
 * the action: by the time a cue was read out the moment it described had gone.
 * Cutting in keeps what the user hears current, and the message that was cut
 * off is stale by definition. Requests that arrive before the engine finishes
 * initializing are held and spoken once, in order, so the first announcement of
 * a session is not lost.
 *
 * The engine is started lazily on the first request and released by shutdown().
 */
object AssistTts {
    private const val TAG = "AssistTts"

    /** A message identical to the previous one within this window is not restarted. */
    private const val REPEAT_WINDOW_MS = 400L

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

    // The last text spoken and when, so a message repeated every frame is not
    // restarted over and over.
    private var lastText: String? = null
    private var lastTime = 0L

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
     * Text is queued behind anything still being spoken, matching how a screen
     * reader reports consecutive changes. Called from the narration thread.
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

            val now = android.os.SystemClock.uptimeMillis()
            if (text == lastText && now - lastTime < REPEAT_WINDOW_MS) {
                return@post
            }
            lastText = text
            lastTime = now

            if (!ready) {
                if (pending.size >= PENDING_LIMIT) {
                    pending.removeFirst()
                }
                pending.addLast(text)
                return@post
            }

            speakNow(text)
        }
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

        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) = Unit

            override fun onDone(utteranceId: String?) = Unit

            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) {
                Log.w(TAG, "Utterance failed: $utteranceId")
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
        // QUEUE_FLUSH: say this now, drop whatever is still speaking, because a
        // message that is still being read describes a moment that has passed.
        val mode = if (flush) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD
        val result = tts.speak(text, mode, null, null)
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
    }
}
