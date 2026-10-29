// Copyright 2026 Dolphin Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
package org.dolphinemu.dolphinemu.assist

import android.content.Context
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log

/**
 * Screen-reader speech output through the platform TextToSpeech engine.
 *
 * Narration runs on its own Python thread, which must never block on speech, so every request is
 * posted to the main looper rather than awaited.
 *
 * What interrupts what is decided by the player's own input and by nothing else. Pressing a control
 * on the screen, on a controller, on a keyboard or on a remote is an event, and it cuts off whatever
 * is being read the moment it happens: the sentence in progress describes a moment the press has
 * just replaced, so it ends on the press itself rather than on the next message, whether or not the
 * game has anything to say afterwards. While at least one control is held down, the messages the
 * game reports are the answer to that press and cut in as well. With nothing held, messages queue up
 * behind each other so that each one can be read to its end. Nothing here counts time: a press and
 * the release that ends it are what the narration listens to, rather than a window guessed at after
 * the press.
 *
 * The engine is used as the system has it -- its own voice, language, rate and pitch. The one
 * exception is an engine that reports no default voice at all, which then reads nothing: for that
 * case a voice is taken from the engine's own list rather than guessed at, so nothing a working
 * engine has been set to is ever replaced.
 *
 * Speech has to work on old and vendor-built systems too, where the engine is not the one Android
 * ships. Two details follow from that: the parameters are always a real Bundle, because engines
 * written against old versions read them without checking for null and die in their own process
 * when given none, which leaves the app silent but running; and a failed start is retried, because
 * an engine that is being installed or restarted reports failure and then works a moment later.
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

    /** How many times the engine may be started before speech is given up on for this session. */
    private const val MAX_ATTEMPTS = 3

    private val mainHandler = Handler(Looper.getMainLooper())

    private var engine: TextToSpeech? = null

    // Kept so a failed start can be tried again from the next message, which arrives through say().
    private var appContext: Context? = null

    // How many times the engine has been started this session, successful or not.
    private var attempts = 0

    // True once onInit has reported success; until then requests are queued.
    private var ready = false

    private val pending = ArrayDeque<String>()

    // The text the engine is reading aloud right now. The game reports some states every frame, and
    // the same words arriving again while they are audible are not restarted.
    private var speakingText: String? = null

    // Controls the player currently holds down, from any device. Nothing counts time; this is the
    // whole of what decides an interruption.
    private var heldInputs = 0

    // Utterance id to the text read under it. Ids are numbers: some engines treat the id as a file
    // name or a log key, and a whole Chinese sentence is the wrong thing to hand them for that.
    private val utteranceTexts = HashMap<String, String>()
    private var nextUtteranceId = 0

    /**
     * Starts the engine now if it was not started yet.
     *
     * Safe to call repeatedly; the second call is a no-op. Must be called from
     * the main thread.
     */
    @JvmStatic
    fun prepare(context: Context) {
        val appContext = context.applicationContext
        this.appContext = appContext

        // A session gets its whole allowance again: an engine that failed to start last time may
        // have been installed or restarted since.
        if (engine == null) {
            attempts = 0
            ready = false
        }

        startEngine(appContext)
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
            if (text == speakingText || text == pending.lastOrNull()) {
                return@post
            }

            if (!ready) {
                if (engine == null) {
                    val context = appContext
                    if (context == null || attempts >= MAX_ATTEMPTS) {
                        Log.w(TAG, "No speech engine available; dropping: $text")
                        return@post
                    }
                    // The message is the moment to try again, since a failure is not final.
                    startEngine(context)
                }
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
        interrupt()
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
            // The next session starts from a fresh allowance, whatever happened in this one.
            attempts = 0
            releaseEngine()
        }
    }

    private fun startEngine(context: Context) {
        if (engine != null || attempts >= MAX_ATTEMPTS) {
            return
        }

        attempts++
        engine = TextToSpeech(context) { status ->
            // This callback arrives on the main thread already.
            if (status == TextToSpeech.SUCCESS) {
                onEngineReady()
            } else {
                // Not treated as final. An engine that is still being installed, or a vendor
                // engine that is restarting, reports exactly this and then works; the next message
                // starts it again, up to MAX_ATTEMPTS. Anything already waiting stays waiting.
                Log.w(TAG, "TextToSpeech initialization failed: $status (attempt $attempts)")
                ready = false
                releaseEngine()
            }
        }
    }

    private fun onEngineReady() {
        ready = true

        val tts = engine ?: return

        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {
                speakingText = utteranceTexts[utteranceId]
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
                val text = utteranceTexts.remove(utteranceId)
                if (text != null && speakingText == text) {
                    speakingText = null
                }
            }
        })

        ensureVoice(tts)

        // Everything collected before the engine existed is spoken now. Only the
        // first one flushes: flushing the rest would leave just the last audible.
        while (pending.isNotEmpty()) {
            speakNow(pending.removeFirst(), flush = pending.isEmpty())
        }
    }

    /**
     * Gives the engine a voice to read with when it claims to have none.
     *
     * A voice the system settings already chose is left untouched: this returns on the first line
     * for every engine that has one. Some old and vendor engines instead report no default voice
     * and then read nothing at all, with speak() succeeding. There is no choice to preserve in that
     * case, so a voice is taken from the engine's own list -- the first one it has for the language
     * it says it is set to, or else the first one it lists. Nothing is assumed about the language.
     */
    private fun ensureVoice(tts: TextToSpeech) {
        if (runCatching { tts.defaultVoice }.getOrNull() != null) {
            return
        }

        val voices = runCatching { tts.voices }.getOrNull()
        if (voices.isNullOrEmpty()) {
            Log.w(TAG, "Engine has no voices at all")
            return
        }

        val language = runCatching { tts.voice?.locale }.getOrNull()
        val voice = voices.firstOrNull { language != null && it.locale == language } ?: voices.first()
        val result = runCatching { tts.voice = voice }.getOrDefault(TextToSpeech.ERROR)
        if (result == TextToSpeech.SUCCESS) {
            Log.i(TAG, "Engine had no default voice; using $voice")
        } else {
            // Still worth speaking: the engine may have been lying about having no voice.
            Log.w(TAG, "Could not set $voice ($result)")
        }
    }

    /**
     * Ends whatever is being read, on the press itself.
     *
     * A press tells the player nothing on its own, so it is tempting to keep reading until the game
     * answers it. That answer can take a moment, and in the meantime the old sentence carries on
     * past the press as if the press had not happened -- which is the one thing a reader cannot
     * check for themselves, since they cannot see what changed. Ending on the press is also what
     * lets the answer, if one comes, arrive as the start of a sentence rather than the middle of
     * the previous one.
     */
    private fun interrupt() {
        val tts = engine ?: return
        tts.stop()
        // The engine reports the stop back through onStop, but the text is already stale: a report
        // of the same state after the press has to be read again, not taken for a repeat.
        speakingText = null
    }

    private fun speakNow(text: String, flush: Boolean = true) {
        val tts = engine ?: return

        // QUEUE_FLUSH: say this now and drop whatever is still speaking, because a
        // message that is still being read describes a moment that has passed.
        val mode = if (flush) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD

        val id = (++nextUtteranceId).toString()
        utteranceTexts[id] = text

        // A Bundle is always passed, empty though it is. Engines written against old versions read
        // the parameters without checking for null; handed none, they throw inside their own
        // process and go quiet for good, while the app carries on working and speak() can still
        // report success. Nothing in it is set, so the engine's own defaults -- voice, language,
        // rate, pitch, output stream -- are exactly what is used.
        val result = tts.speak(text, mode, Bundle(), id)
        if (result != TextToSpeech.SUCCESS) {
            utteranceTexts.remove(id)
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
        utteranceTexts.clear()
    }
}
