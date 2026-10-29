// Copyright 2026 Dolphin Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
package org.dolphinemu.dolphinemu.assist

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.dolphinemu.dolphinemu.launcher.AssistAssets
import java.io.File
import java.io.IOException
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Owns the narration runtime: the embedded Python interpreter, its worker
 * thread, and the command queue the in-game menu feeds.
 *
 * The Python side does all of the reading. It asks this object for the guest
 * RAM addresses, for the next menu command, and for speech and cue playback,
 * through the static methods below. Everything here is a thin forwarder, so the
 * Python code stays independent of Android specifics.
 *
 * The worker is started and stopped with the assist preference. Stopping it
 * posts a command rather than interrupting the thread, so a message being read
 * out finishes on its own.
 */
object AssistRuntime {
    private const val TAG = "AssistRuntime"

    /** Menu commands the Python loop understands. */
    const val COMMAND_REPEAT = "repeat"
    const val COMMAND_PAUSE = "pause"
    const val COMMAND_DESCRIPTION = "description"
    const val COMMAND_STOP = "stop"

    private val mainHandler = Handler(Looper.getMainLooper())

    private val commands = ConcurrentLinkedQueue<String>()

    private val running = AtomicBoolean(false)

    @Volatile
    private var worker: Thread? = null

    /**
     * Whether a worker is currently alive.
     */
    @JvmStatic
    fun isRunning(): Boolean = running.get()

    /**
     * Starts the worker if it is not running.
     *
     * Called from the emulation activity when assist speech is enabled. Safe to
     * call more than once; the second call does nothing.
     */
    fun start(context: Context) {
        if (!running.compareAndSet(false, true)) {
            return
        }

        val appContext = context.applicationContext
        assistContext = appContext
        AssistTts.prepare(appContext)

        commands.clear()

        val thread = Thread({
            try {
                startInterpreter(appContext)
                // Expanded before the reader starts, because it reads the files
                // by path and would otherwise have to know the APK layout.
                val directory = assistDirectory(appContext)
                if (directory == null) {
                    throw IOException("Narration data unavailable")
                }
                val module = Python.getInstance().getModule("wiirhdiag.assist")
                module.callAttr("run", directory)
            } catch (e: Exception) {
                Log.e(TAG, "Narration worker failed", e)
                // Nothing the player pressed asked for this, so it is not a reply and waits its
                // turn rather than cutting in.
                AssistTts.say("Speech assist stopped: ${e.message}", flush = false)
            } finally {
                running.set(false)
                worker = null
            }
        }, "assist-narration")

        // Below normal priority: reading must never compete with the emulator.
        thread.priority = Thread.NORM_PRIORITY - 1
        worker = thread
        thread.start()
    }

    /**
     * Asks the worker to finish.
     *
     * Called when assist speech is turned off or emulation stops. The worker
     * notices on its next poll and exits on its own.
     */
    fun stop() {
        if (!running.get()) {
            return
        }

        commands.offer(COMMAND_STOP)
        AssistTts.clear()
        AssistAudio.stop()
    }

    /**
     * Releases speech and audio for good. Called when the activity is destroyed.
     */
    fun shutdown() {
        stop()
        val thread = worker
        worker = null
        if (thread != null && thread.isAlive) {
            thread.join(2000)
        }
        AssistTts.shutdown()
        AssistAudio.shutdown()
    }

    /**
     * Queues one menu command for the worker.
     */
    @JvmStatic
    fun pushCommand(name: String) {
        commands.offer(name)
    }

    private fun startInterpreter(context: Context) {
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context))
        }
    }

    /**
     * The directory holding the narration data files, or null when they are not
     * on disk and cannot be expanded.
     *
     * The launcher expands them out of the APK, but the emulation activity can
     * be started from an intent without the launcher ever running, so the copy
     * is completed here as well. It is cheap to check once a stamp matches.
     */
    @JvmStatic
    fun assistDirectory(context: Context): String? {
        val dir = AssistAssets.installDir(context) ?: return null
        if (AssistAssets.isInstalled(context)) {
            return dir.absolutePath
        }

        return try {
            AssistAssets.ensureInstalled(context) {}
            dir.absolutePath
        } catch (e: IOException) {
            Log.e(TAG, "Narration data could not be expanded", e)
            null
        }
    }

    // The methods below are called from Python through jclass(), so they must
    // stay @JvmStatic and keep their names.

    /**
     * Loads the kungfu cue sounds and the feedback tone from the Python data
     * directory.
     *
     * The Python side passes the directory it already resolved for its own data
     * files, so this does not need to know how the interpreter was laid out.
     * Called once, before the reading loop starts.
     */
    @JvmStatic
    fun prepareCues(directory: String) {
        val files = HashMap<String, String>()
        for (distance in listOf("near", "far")) {
            for (player in 1..2) {
                val path = "$directory/kungfu-$distance-p$player.wav"
                if (File(path).isFile) {
                    files["$distance:$player"] = path
                }
            }
        }

        // The feedback tone is generated by the Python side beside the cue
        // sounds, and is played through the same pool.
        val notice = "$directory/diagnostic-notice.wav"
        if (File(notice).isFile) {
            files["notice:0"] = notice
        }

        if (files.isEmpty()) {
            Log.i(TAG, "No kungfu cue sounds found in $directory")
            return
        }

        // Python calls this from the narration thread; the audio pool has to be
        // built on the main thread, so the loading is handed over.
        mainHandler.post {
            val context = assistContext
            if (context == null) {
                Log.w(TAG, "No context yet; cue sounds not loaded")
                return@post
            }
            AssistAudio.prepare(context, files)
        }
    }

    // Set by start() so prepareCues, which Python calls from its own thread, can
    // reach the application context without one being passed across the bridge.
    @Volatile
    private var assistContext: Context? = null

    /**
     * True once the guest RAM mapping exists and can be read.
     */
    @JvmStatic
    fun gameRamReady(): Boolean = AssistNative.GetMem1Size() > 0L

    /**
     * Host address of MEM1, or 0 when no game is running.
     */
    @JvmStatic
    fun mem1Address(): Long = AssistNative.GetMem1Address()

    /**
     * Usable byte count of MEM1, or 0 when no game is running.
     */
    @JvmStatic
    fun mem1Size(): Long = AssistNative.GetMem1Size()

    /**
     * Host address of MEM2, or 0 when no game is running.
     */
    @JvmStatic
    fun mem2Address(): Long = AssistNative.GetMem2Address()

    /**
     * Usable byte count of MEM2, or 0 when no game is running.
     */
    @JvmStatic
    fun mem2Size(): Long = AssistNative.GetMem2Size()

    /**
     * Next pending menu command, or an empty string when the queue is empty.
     *
     * Empty rather than null keeps the Python side to a simple string test.
     */
    @JvmStatic
    fun pollCommand(): String = commands.poll() ?: ""

    /**
     * Speaks one message through the system screen reader voice.
     *
     * [flush] is set by the reader when the message answers a press the player made on a screen
     * where the game was waiting for one, so that it replaces the stale sentence instead of
     * queueing behind it. Everything else is narration and queues.
     */
    @JvmStatic
    fun speak(text: String, flush: Boolean) {
        AssistTts.say(text, flush)
    }

    /**
     * Reports whether the screen the player is on waits for a button, as the reader sees it every
     * frame. It is what decides whether a press other than the d-pad may cut speech short.
     */
    @JvmStatic
    fun setAwaitingInput(awaiting: Boolean) {
        AssistTts.setAwaitingInput(awaiting)
    }

    /**
     * Returns how many presses happened since the reader last asked, and forgets them.
     *
     * The reader pairs this with the messages of the same frame: a message produced by a press is
     * the game's answer to it.
     */
    @JvmStatic
    fun takePresses(): Int = AssistTts.takePresses()

    /**
     * Drops speech that has not started, so a stale cue is not read out.
     */
    @JvmStatic
    fun clearSpeech() {
        AssistTts.clear()
    }

    /**
     * Plays one kungfu cue by the name the Python cues use.
     */
    @JvmStatic
    fun playCue(name: String, loop: Boolean) {
        AssistAudio.play(name, loop)
    }

    /**
     * Stops the cue that is playing.
     */
    @JvmStatic
    fun stopCue() {
        AssistAudio.stop()
    }

    /**
     * Writes a diagnostic line to logcat.
     */
    @JvmStatic
    fun log(message: String) {
        Log.d(TAG, message)
    }
}
