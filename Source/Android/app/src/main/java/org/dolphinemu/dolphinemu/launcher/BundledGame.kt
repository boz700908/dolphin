// SPDX-License-Identifier: GPL-2.0-or-later

package org.dolphinemu.dolphinemu.launcher

import android.content.Context
import org.dolphinemu.dolphinemu.utils.Log
import java.io.File
import java.io.IOException

/**
 * The single game this build is a launcher for.
 *
 * The game image is shipped inside the APK as an asset. Assets are not real files, and the
 * emulator core can only boot from a real path, so the image is copied out to the app's external
 * files directory the first time it is needed. Later launches reuse the copy.
 *
 * Nothing here describes the game itself: the title, the artwork and the game ID all come from the
 * emulator's own disc parser once the file exists (see LauncherActivity). Only the asset name and
 * the expected size are needed, to know whether the copy already happened.
 *
 * When the bundled game changes, update [ASSET_NAME] and [SIZE_BYTES] here.
 */
object BundledGame {
    const val ASSET_NAME = "rhythm_heaven_fever_sc.wbfs"

    /** Expected size in bytes; a mismatch means the copy is stale and has to be redone. */
    private const val SIZE_BYTES = 486539264L

    private const val COPY_BUFFER_BYTES = 1 shl 20

    /** Where the extracted image lives, or null if external storage is unavailable. */
    fun installDir(context: Context): File? {
        val base = context.getExternalFilesDir(null) ?: context.filesDir ?: return null
        return File(base, "Game")
    }

    fun installedFile(context: Context): File? =
        installDir(context)?.let { File(it, ASSET_NAME) }

    fun isInstalled(context: Context): Boolean {
        val file = installedFile(context) ?: return false
        return file.isFile && file.length() == SIZE_BYTES
    }

    /** Bytes the image needs, for the out-of-space message. */
    fun requiredBytes(): Long = SIZE_BYTES

    /**
     * Makes sure the bundled image exists as a real file and returns it.
     *
     * Runs blocking I/O, so call it off the UI thread. [onProgress] receives a value from 0 to 100
     * and may be called from this thread.
     */
    @Throws(IOException::class)
    fun ensureInstalled(context: Context, onProgress: (Int) -> Unit): File {
        val target = installedFile(context) ?: throw IOException("External storage unavailable")
        if (target.isFile && target.length() == SIZE_BYTES) {
            return target
        }

        val dir = target.parentFile ?: throw IOException("External storage unavailable")
        if (!dir.isDirectory && !dir.mkdirs()) {
            throw IOException("Could not create ${dir.absolutePath}")
        }

        // Write to a temporary file first so an interrupted copy is never mistaken for a good one.
        val temp = File(dir, "$ASSET_NAME.part")
        if (temp.exists() && !temp.delete()) {
            throw IOException("Could not replace ${temp.absolutePath}")
        }

        Log.debug("[BundledGame] Extracting $ASSET_NAME to ${target.absolutePath}")

        var copied = 0L
        var lastPercent = -1
        context.assets.open(ASSET_NAME).use { input ->
            temp.outputStream().use { output ->
                val buffer = ByteArray(COPY_BUFFER_BYTES)
                while (true) {
                    val read = input.read(buffer)
                    if (read <= 0) {
                        break
                    }
                    output.write(buffer, 0, read)
                    copied += read
                    val percent = (copied * 100 / SIZE_BYTES).toInt().coerceIn(0, 100)
                    if (percent != lastPercent) {
                        lastPercent = percent
                        onProgress(percent)
                    }
                }
                output.flush()
            }
        }

        if (target.exists() && !target.delete()) {
            throw IOException("Could not replace ${target.absolutePath}")
        }
        if (!temp.renameTo(target)) {
            throw IOException("Could not finalize ${target.absolutePath}")
        }

        onProgress(100)
        return target
    }
}
