// Copyright 2026 Dolphin Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
package org.dolphinemu.dolphinemu.assist

import android.content.Context
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import java.util.concurrent.atomic.AtomicInteger

/**
 * Screen-reader speech output through the platform TextToSpeech engine.
 *
 * Narration runs on its own Python thread, which must never block on speech, so every request is
 * posted to the main looper and never awaited.
 *
 * A press on its own ends nothing. What the player did is only recorded: its time, and that it
 * happened at all. A sentence being read therefore carries on through a button, a direction or a
 * flick, which is what a player needs while a level is running and every press is part of the
 * play rather than an answer to anything on screen.
 *
 * What replaces a sentence is the next one, and only when the reader says it is the answer to a
 * press. Then it is heard from its start, in place of the stale text, because the reader cannot
 * see the screen and cannot tell that the words it was reading describe a moment that has passed.
 * That single case is the whole of the interrupting rule, and the reader decides it per message
 * from what is on screen and from the presses it took, never from timing.
 *
 * Everything else waits its turn. The engine is given one message at a time and the rest stand
 * here, in the order they were announced, so that a cut drops what was not reached and re-reading
 * a queued message is never needed to put the queue back together. While the player is busy --
 * within a short window of the last press -- only the newest waiting message is kept: a level
 * announces far faster than it can be read aloud, and the older ones describe states the player
 * has already moved past, so keeping them would leave the reader behind the game.
 *
 * The window is the one piece of timing here and it decides nothing on its own: a press inside it
 * only limits how much may wait, never what may be cut. Nothing else counts time except a
 * fallback for an engine that never reports finishing what it reads, which would otherwise leave
 * the queue standing for ever.
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

    /**
     * How long after a press the player still counts as playing.
     *
     * Only decides how many messages may wait behind the audible one, never what may be cut. The
     * game confirms a change over more than one frame, so a message produced by a press can arrive
     * a little later than the press itself.
     */
    private const val INPUT_GRACE_MS = 700L

    /** How many messages may wait behind the audible one while the player is playing. */
    private const val INTERACTIVE_QUEUE_LIMIT = 1

    /** Upper bound on the fallback for an engine that never reports finishing. */
    private const val MAX_FALLBACK_MS = 60_000L

    private val mainHandler = Handler(Looper.getMainLooper())

    private var engine: TextToSpeech? = null

    // Kept so a failed start can be tried again from the next message, which arrives through say().
    private var appContext: Context? = null

    // How many times the engine has been started this session, successful or not.
    private var attempts = 0

    // True once onInit has reported success; until then requests are held.
    private var ready = false

    // Messages announced before the engine was ready, in the order they were announced.
    private val pending = ArrayDeque<String>()

    // Messages announced and not handed to the engine yet. The engine reads one at a time, so this
    // is the whole of the queue and cutting is dropping it: nothing was given over that would have
    // to be stopped one message at a time.
    private val queue = ArrayDeque<String>()

    // What the engine was given and has not reported finishing. The game reports some states every
    // frame, and the same words arriving again while they are being read are not restarted.
    private var speakingText: String? = null
    private var speakingId: String? = null

    // When the last press happened, by the clock that keeps running in sleep.
    private var lastInputAt = 0L

    // The fallback timer for the message being read, kept so it can be cancelled.
    private var watchdog: Runnable? = null

    // Presses since the reader last looked. Written on the main thread, read from the narration
    // thread, which is the one that pairs a message with the press that produced it.
    private val presses = AtomicInteger(0)

    // The direction a controller's d-pad hat was last seen pointing in, per axis, so that holding
    // it does not read as repeated presses.
    private var hatX = 0
    private var hatY = 0

    // Utterance id to what was last handed under it. Ids are numbers: some engines treat the id as
    // a file name or a log key, and a whole Chinese sentence is the wrong thing to hand them.
    private var nextUtteranceId = 0

    /**
     * Starts the engine now if it was not started yet.
     *
     * Safe to call repeatedly; the second call is a no-op. Must be called from the main thread.
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
     * Called from the narration thread. [flush] is the reader's finding about this message alone:
     * that it is the game's answer to a press the player made, and so replaces the stale sentence
     * instead of queueing behind it. Every other message waits its turn, whatever the player is
     * doing with their hands.
     */
    @JvmStatic
    fun say(text: String, flush: Boolean) {
        if (text.isEmpty()) {
            return
        }

        mainHandler.post {
            if (text == speakingText || queue.contains(text) || pending.contains(text)) {
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

            speakNow(text, flush)
        }
    }

    /**
     * Records that a control went down, whichever device it is on. Called from the touch layer, the
     * key dispatch of the emulation activity and the gesture layer, all of which run on the main
     * thread.
     *
     * Nothing is cut here. A press is the player's own doing and says nothing about what is being
     * read: it may be starting a round, moving through a level or answering a question, and only
     * the reader, which sees what the screen does next, can tell which. What the press does leave
     * behind is the moment it happened, which the reader pairs with the message it produced.
     */
    @JvmStatic
    fun noteInputDown() {
        lastInputAt = SystemClock.uptimeMillis()
        presses.incrementAndGet()
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
        mainHandler.post { cutAll() }
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
                // Nothing to do: what is being read was recorded when it was handed over, and an
                // engine that reports a start late must not disturb the message read after it.
            }

            override fun onDone(utteranceId: String?) {
                finished(utteranceId)
            }

            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) {
                Log.w(TAG, "Utterance failed: $utteranceId")
                finished(utteranceId)
            }

            override fun onStop(utteranceId: String?, interrupted: Boolean) {
                // Only the message that was cut is answered here; one stopped because the whole
                // queue went with it was already forgotten before the engine was told to stop.
                finished(utteranceId)
            }
        })

        ensureVoice(tts)

        // Everything announced before the engine existed is spoken now, in the order it was
        // announced: they queue behind each other exactly as they would have.
        while (pending.isNotEmpty()) {
            speakNow(pending.removeFirst(), flush = false)
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
     * Decides what a newly announced message does to what is already there.
     *
     * An answer replaces: the reader said this message is what the game made of the player's press,
     * so the sentence still being read describes the moment before it and the messages waiting
     * behind it describe moments the player has moved past. All of them go, and the answer is heard
     * from its start.
     *
     * Anything else waits. That is the whole point of not cutting on a press: a hint, a lyric, a
     * karate cue or a score is announced while the level runs and the player is pressing all the
     * while, and none of those presses is an answer to it. What a press does change is how much may
     * wait -- while the player is playing, the older waiting messages are dropped and only the
     * newest is kept, so the reading follows the game instead of falling behind it.
     */
    private fun speakNow(text: String, flush: Boolean) {
        if (flush) {
            queue.clear()
            stopSpeaking()
            start(text)
            return
        }

        if (speakingText != null || queue.isNotEmpty()) {
            if (inputIsRecent()) {
                while (queue.size >= INTERACTIVE_QUEUE_LIMIT) {
                    queue.removeFirst()
                }
            }
            queue.addLast(text)
            return
        }

        start(text)
    }

    /**
     * Hands one message to the engine.
     *
     * The engine is given one at a time, so this always flushes: whatever it held is either gone
     * already or was cut a moment ago, and nothing is ever added behind a message that is still
     * being read. A queue of one is also what makes an engine that was stopped without saying so
     * harmless, since the next message starts from an empty one either way.
     */
    private fun start(text: String) {
        val tts = engine ?: return
        val id = (++nextUtteranceId).toString()

        speakingId = id
        speakingText = text
        scheduleWatchdog(id, text)

        // A Bundle is always passed, empty though it is. Engines written against old versions read
        // the parameters without checking for null; handed none, they throw inside their own
        // process and go quiet for good, while the app carries on working and speak() can still
        // report success. Nothing in it is set, so the engine's own defaults -- voice, language,
        // rate, pitch, output stream -- are exactly what is used.
        val result = tts.speak(text, TextToSpeech.QUEUE_FLUSH, Bundle(), id)
        if (result != TextToSpeech.SUCCESS) {
            Log.w(TAG, "Speak rejected ($result): $text")
            finishCurrent()
        }
    }

    /**
     * Moves on to the next message, because the one handed over was read to its end, failed, or was
     * given longer than it could possibly need.
     */
    private fun finished(utteranceId: String?) {
        if (utteranceId != speakingId) {
            return
        }
        finishCurrent()
    }

    private fun finishCurrent() {
        cancelWatchdog()
        speakingId = null
        speakingText = null
        if (queue.isEmpty()) {
            return
        }
        start(queue.removeFirst())
    }

    /**
     * Ends the message being read without moving on.
     *
     * What was waiting goes on to be read after the new message, which is why nothing has to be put
     * back: it was never handed to the engine.
     */
    private fun stopSpeaking() {
        cancelWatchdog()
        speakingId = null
        speakingText = null
        engine?.stop()
    }

    /**
     * Drops everything, audible and waiting alike.
     *
     * Used when narration is paused or the game exits: none of it describes anything the player
     * will still be able to act on afterwards.
     */
    private fun cutAll() {
        queue.clear()
        pending.clear()
        stopSpeaking()
    }

    /**
     * Starts the fallback for a message the engine may never report.
     *
     * Nothing here is expected to be used: the engine says when it is done. The ones that never say
     * it -- old and vendor engines, or one that died quietly mid-sentence -- would otherwise leave
     * the queue standing for the rest of the session, so a message is given well over the time it
     * takes to read, and the next one follows if the report never comes.
     */
    private fun scheduleWatchdog(id: String, text: String) {
        cancelWatchdog()
        val task = Runnable {
            if (id == speakingId) {
                Log.w(TAG, "No end reported; moving on")
                finishCurrent()
            }
        }
        watchdog = task
        mainHandler.postDelayed(task, fallbackDelay(text))
    }

    private fun cancelWatchdog() {
        watchdog?.let { mainHandler.removeCallbacks(it) }
        watchdog = null
    }

    /**
     * How long a message may take to read before the queue is moved on without the engine.
     *
     * Chinese and Latin text are read at different speeds, and the engine's own rate is unknown
     * here, so the estimate is generous on purpose: arriving late only leaves a pause, while
     * arriving early would talk over a message still being read.
     */
    private fun fallbackDelay(text: String): Long {
        var cjk = 0
        for (ch in text) {
            if (ch.code in 0x3400..0x9FFF) {
                cjk++
            }
        }
        val other = text.length - cjk
        val spoken = (cjk * 145L + other * 70L + 500L) * 3 / 4
        return (spoken * 2 + 2000L).coerceAtMost(MAX_FALLBACK_MS)
    }

    /** Whether the player pressed something recently enough to still count as playing. */
    private fun inputIsRecent(): Boolean =
        lastInputAt != 0L && SystemClock.uptimeMillis() - lastInputAt <= INPUT_GRACE_MS

    private fun releaseEngine() {
        stopSpeaking()
        queue.clear()
        engine?.shutdown()
        engine = null
        presses.set(0)
        lastInputAt = 0L
    }
}
