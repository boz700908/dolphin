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
 * A press ends the sentence in progress only while that sentence is itself waiting for one: a
 * dialogue line, a grade, a letter in the mailbox, a menu. There the press is an answer, and what
 * the game makes of it is a new message a frame later, which is read from its start because the
 * stale text was already dropped. A message that is not waiting -- the hints a karate round gives
 * and repeats, a lyric line, an endless score, anything a level draws while it is being played --
 * is read to its end, because a button, a direction or a flick made during play is part of the
 * play and not an answer to anything.
 *
 * The decision is made per message and not per screen, because one screen can carry both kinds at
 * once: a lesson showing a dialogue box is also the screen the round's own hints appear on, and
 * stopping the engine to answer the box would silence them too. So each message is handed here
 * with the reader's finding about that message alone, and a press cuts what is being read and
 * nothing else. What the engine had not started yet is given back to it afterwards, minus the
 * messages that were themselves waiting: those describe the moment the press has moved past.
 *
 * Whether a message waits is not guessed here. The Python reader knows it from what is actually on
 * screen and says so about every message it hands over; the reader also takes the press count, so
 * a message that answers a press is spoken queued behind nothing instead of behind stale text.
 * Nothing counts time: a press, and what is on screen when it happens, are the whole of it.
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
     * One message handed to the engine: its words, and whether that message waits for a press.
     */
    private class Utterance(val text: String, val waits: Boolean, var id: String? = null)

    private val mainHandler = Handler(Looper.getMainLooper())

    private var engine: TextToSpeech? = null

    // Kept so a failed start can be tried again from the next message, which arrives through say().
    private var appContext: Context? = null

    // How many times the engine has been started this session, successful or not.
    private var attempts = 0

    // True once onInit has reported success; until then requests are queued.
    private var ready = false

    // Messages waiting for an engine that is not ready yet. A flush asked for before the engine
    // existed is not one any more by the time it is spoken: nothing is audible for it to replace.
    private val pending = ArrayDeque<Utterance>()

    // What the engine has been handed, in the order it will read it, so that the front is the
    // utterance audible now. The engine's own queue cannot be inspected and a stop drops all of it,
    // which is why the list is kept here: what was behind the front has not started, and is handed
    // back after a stop that only meant to cut one message.
    private val pushed = ArrayDeque<Utterance>()

    // The text the engine is reading aloud right now. The game reports some states every frame, and
    // the same words arriving again while they are audible are not restarted.
    private var speakingText: String? = null

    // Whether the utterance being read is one that waits for a press.
    private var audibleWaits = false

    // Presses since the reader last looked. Written on the main thread, read from the narration
    // thread, which is the one that knows whether the game is waiting for the player.
    private val presses = AtomicInteger(0)

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
     * answers a press the player just made. [waits] is the reader's finding about this message
     * alone: whether it stays on screen until a press moves it on, and so may be cut by one.
     */
    @JvmStatic
    fun say(text: String, flush: Boolean, waits: Boolean) {
        if (text.isEmpty()) {
            return
        }

        mainHandler.post {
            if (text == speakingText || text == pushed.lastOrNull()?.text) {
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
                pending.addLast(Utterance(text, waits))
                return@post
            }

            speakNow(text, flush, waits)
        }
    }

    /**
     * Records that a control went down, whichever device it is on. Called from the touch layer, the
     * key dispatch of the emulation activity and the gesture layer, all of which run on the main
     * thread.
     *
     * A press is only ever an answer to a message that waits for one, so what it interrupts depends
     * on that alone and not on which control it was: a d-pad direction, a flick and a button are
     * all the same here. See the class comment.
     */
    @JvmStatic
    fun noteInputDown() {
        presses.incrementAndGet()

        // The message being read decides, not the screen it is on: a lesson can show a dialogue box
        // and the round's own hints at the same time, and only the box is what the press answers.
        if (audibleWaits) {
            cutAudible()
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
                speakingText = utteranceTexts[utteranceId]
                // Only ever from the message itself: an engine that reports a start for something
                // the list no longer holds must not make the one at its head unanswerable.
                pushed.firstOrNull { it.id == utteranceId }?.let { audibleWaits = it.waits }
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
                    audibleWaits = false
                }
                pushed.removeAll { it.id == utteranceId }
            }
        })

        ensureVoice(tts)

        // Everything collected before the engine existed is spoken now, in the order it was
        // announced. A flush among them would empty the queue it is standing in, so these are
        // simply queued: nothing is audible yet for them to replace.
        while (pending.isNotEmpty()) {
            val utterance = pending.removeFirst()
            speakNow(utterance.text, flush = false, waits = utterance.waits)
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
     * Ends the message being read, because a press answered it, and keeps the rest.
     *
     * A press tells the player nothing on its own, so it is tempting to keep reading until the game
     * answers it. That answer can take a moment, and in the meantime the old sentence carries on
     * past the press as if the press had not happened -- which is the one thing a reader cannot
     * check for themselves, since they cannot see what changed. Ending on the press is also what
     * lets the answer, if one comes, arrive as the start of a sentence rather than the middle of
     * the previous one.
     *
     * Only that one message goes. The engine cannot drop part of its queue, so what it had not
     * reached yet is taken out of the list here, handed back once the stop has landed, and lost
     * only where it was itself waiting for a press: those describe the moment the press has moved
     * past, while a hint, a lyric or a score standing in the same queue is still worth hearing.
     */
    private fun cutAudible() {
        val keep = pushed.drop(1).filter { !it.waits }

        pushed.clear()
        pending.clear()
        speakingText = null
        syncAudible()

        engine?.stop()

        for (utterance in keep) {
            speakNow(utterance.text, flush = false, waits = utterance.waits)
        }
    }

    /**
     * Drops everything, audible and queued alike.
     *
     * Used when narration is paused or the game exits: none of it describes anything the player
     * will still be able to act on afterwards.
     */
    private fun cutAll() {
        pushed.clear()
        pending.clear()
        speakingText = null
        syncAudible()
        engine?.stop()
    }

    /**
     * Points what a press would answer at the head of the engine's queue.
     *
     * Kept as the queue changes rather than taken from onStart, because an engine is not obliged to
     * report a start, and the message at the head is the one a press meets either way.
     */
    private fun syncAudible() {
        audibleWaits = pushed.firstOrNull()?.waits ?: false
    }

    private fun speakNow(text: String, flush: Boolean, waits: Boolean) {
        val tts = engine ?: return

        // QUEUE_FLUSH: say this now and drop whatever is still speaking, because a message that is
        // still being read describes a moment that has passed. QUEUE_ADD: this is narration nobody
        // asked for, so it waits until what is audible has been read to its end.
        //
        // Nothing left in the engine counts as a flush without being asked for. A message added to
        // an empty queue is otherwise held until the next one is spoken after it, and the two are
        // then heard together -- which is what an engine that was stopped out of an utterance and
        // never said so leaves behind. Queued here means handed over and not yet reached, so an
        // empty list is exactly the case where there is nothing to queue behind.
        val mode = if (flush || pushed.isEmpty()) {
            TextToSpeech.QUEUE_FLUSH
        } else {
            TextToSpeech.QUEUE_ADD
        }

        if (mode == TextToSpeech.QUEUE_FLUSH) {
            // What was audible is gone from the engine, whether or not it reports as much.
            pushed.clear()
            speakingText = null
        }

        val utterance = Utterance(text, waits)
        val id = (++nextUtteranceId).toString()
        utterance.id = id
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
        } else {
            pushed.addLast(utterance)
        }
        syncAudible()
    }

    private fun releaseEngine() {
        engine?.let {
            it.stop()
            it.shutdown()
        }
        engine = null
        speakingText = null
        pushed.clear()
        audibleWaits = false
        presses.set(0)
        utteranceTexts.clear()
    }
}
