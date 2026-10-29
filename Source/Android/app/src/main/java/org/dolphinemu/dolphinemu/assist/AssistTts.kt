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
import java.util.concurrent.atomic.AtomicInteger

/**
 * Screen-reader speech output through the platform TextToSpeech engine.
 *
 * Narration runs on its own Python thread, which must never block on speech, so every request is
 * posted to the main looper rather than awaited.
 *
 * What interrupts what is decided by the player's own input and by nothing else. Every device
 * reports here -- the gesture layer, the on-screen overlay, a controller, a keyboard, a TV remote
 * -- so which one was used never enters into it, and no press is treated differently from another.
 *
 * A press ends the sentence in progress only while the game is waiting for the player: a dialogue
 * line, a grade, a letter in the mailbox, a menu. There the press is an answer, and what the game
 * makes of it is a new message a frame later, which is read from its start because the stale text
 * was already dropped. While the game is being played rather than waiting for the player --
 * including while a level is showing its own instructions, such as the hints a karate round gives
 * and repeats -- a button, a direction or a flick is all part of the play itself, so narration is
 * allowed to finish and nothing is cut off.
 *
 * Whether the game waits is not guessed here. The Python reader knows it from what is actually on
 * screen and reports it every frame; the reader also takes the press count, so a message that
 * answers a press is spoken queued behind nothing instead of behind stale text. Nothing counts
 * time: a press, and the state the game is in when it happens, are the whole of the decision.
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

    /** How far a d-pad hat has to travel before it counts as pointing that way. */
    private const val HAT_DEADZONE = 0.3f

    private val mainHandler = Handler(Looper.getMainLooper())

    private var engine: TextToSpeech? = null

    // Kept so a failed start can be tried again from the next message, which arrives through say().
    private var appContext: Context? = null

    // How many times the engine has been started this session, successful or not.
    private var attempts = 0

    // True once onInit has reported success; until then requests are queued.
    private var ready = false

    // Messages and whether each one was meant to replace what is audible. A flush asked for before
    // the engine existed is still a flush when it is spoken.
    private val pending = ArrayDeque<Pair<String, Boolean>>()

    // The text the engine is reading aloud right now. The game reports some states every frame, and
    // the same words arriving again while they are audible are not restarted.
    private var speakingText: String? = null

    // Presses since the reader last looked. Written on the main thread, read from the narration
    // thread, which is the one that knows whether the game is waiting for the player.
    private val presses = AtomicInteger(0)

    // Reported by the reader every frame: true while what is on screen is a screen that waits for a
    // button. A press is an answer there and may cut in; during play it may not, even when the
    // level is showing instructions. A level's dialogue box is the one exception among its own
    // drawings: its words stay up until a button moves the story on.
    @Volatile
    private var awaitingInput = false

    // The direction a controller's d-pad hat was last seen pointing in, per axis, so that holding
    // it does not read as repeated presses.
    private var hatX = 0
    private var hatY = 0

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
     * Called from the narration thread. [flush] asks for the message to replace whatever is
     * audible instead of queueing behind it, which the reader asks for only when the message
     * answers a press the player just made while the game was waiting for one.
     */
    @JvmStatic
    fun say(text: String, flush: Boolean) {
        if (text.isEmpty()) {
            return
        }

        mainHandler.post {
            val last = pending.lastOrNull()?.first
            if (text == speakingText || text == last) {
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
                pending.addLast(text to flush)
                return@post
            }

            speakNow(text, flush)
        }
    }

    /**
     * Records that a control went down, whichever device it is on. Called from the touch layer, the
     * key dispatch of the emulation activity and the gesture layer, all of which run on the main
     * thread.
     *
     * A press is only ever an answer to a screen that waits for one, so what it interrupts depends
     * on that alone and not on which control it was: a d-pad direction, a flick and a button are
     * all the same here. See the class comment.
     */
    @JvmStatic
    fun noteInputDown() {
        presses.incrementAndGet()

        if (awaitingInput) {
            interrupt()
        }
    }

    /**
     * Records what a controller's d-pad hat is doing, reported as an axis rather than as keys.
     *
     * A push that stays pushed is one press and not a stream of them, so only the change counts:
     * the hat is remembered by the direction it points in, and a change to another one is a new
     * press.
     */
    @JvmStatic
    fun noteHatAxis(x: Float, y: Float) {
        val sx = if (x > HAT_DEADZONE) 1 else if (x < -HAT_DEADZONE) -1 else 0
        val sy = if (y > HAT_DEADZONE) 1 else if (y < -HAT_DEADZONE) -1 else 0

        if (sx == 0 && sy == 0) {
            hatX = 0
            hatY = 0
            return
        }
        if (sx == hatX && sy == hatY) {
            return
        }

        hatX = sx
        hatY = sy
        noteInputDown()
    }

    /**
     * Sets whether the game is waiting for the player, as the reader reports it every frame.
     */
    @JvmStatic
    fun setAwaitingInput(value: Boolean) {
        awaitingInput = value
    }

    /**
     * Returns how many presses have happened since this was last called, and forgets them.
     *
     * Called by the reader once per frame: a press belongs to the message it produced, so the
     * count is emptied even on frames that produced none.
     */
    @JvmStatic
    fun takePresses(): Int = presses.getAndSet(0)

    /**
     * Drops speech that has not started yet, and ends the one in progress.
     *
     * Used when the game exits or narration is paused, so stale cues are not read out later.
     */
    @JvmStatic
    fun clear() {
        mainHandler.post { interrupt() }
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

        // Everything collected before the engine existed is spoken now, in the order it was
        // announced. A flush among them would empty the queue it is standing in, so these are
        // simply queued: nothing is audible yet for them to replace.
        while (pending.isNotEmpty()) {
            speakNow(pending.removeFirst().first, flush = false)
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
        // Queued messages describe states the press has just moved past, so they go with it.
        pending.clear()
        // The engine reports the stop back through onStop, but the text is already stale: a report
        // of the same state after the press has to be read again, not taken for a repeat.
        speakingText = null
        engine?.stop()
    }

    private fun speakNow(text: String, flush: Boolean) {
        val tts = engine ?: return

        // QUEUE_FLUSH: say this now and drop whatever is still speaking, because a message that is
        // still being read describes a moment that has passed. QUEUE_ADD: this is narration nobody
        // asked for, so it waits until what is audible has been read to its end.
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
        presses.set(0)
        awaitingInput = false
        utteranceTexts.clear()
    }
}
