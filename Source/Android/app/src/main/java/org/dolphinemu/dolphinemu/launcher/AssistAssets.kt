// Copyright 2026 Dolphin Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

package org.dolphinemu.dolphinemu.launcher

import android.content.Context
import org.dolphinemu.dolphinemu.utils.Log
import java.io.File
import java.io.IOException

/**
 * The narration data files this build ships, copied out beside the game image.
 *
 * The Python reader needs these as real files: it hashes them, reads the glyph font as raw
 * bytes, and hands the cue sounds to the platform audio mixer. The interpreter can only see its
 * own packaged assets, so the files are expanded into the app's external files directory in the
 * same pass that expands the bundled game.
 *
 * Nothing here knows what the files mean. The set is whatever is under `assets/assist`, and a
 * stamp records the APK install it came from, so replacing the files in the APK refreshes the copy
 * on the next launch without reading 25 MiB to compare contents.
 */
object AssistAssets {
    /** Asset subdirectory holding the narration data. */
    private const val ASSET_DIR = "assist"

    /** Written last, outside the asset list, so a half-finished copy never looks complete. */
    private const val STAMP_NAME = ".installed"

    /**
     * Size of the asset set in bytes, for the out-of-space message. Only a total is needed, so it
     * is a constant rather than a scan of every asset on the way to launching a game.
     */
    private const val SIZE_BYTES = 25_135_697L

    private const val COPY_BUFFER_BYTES = 1 shl 16

    /** Where the expanded data lives, or null if external storage is unavailable. */
    fun installDir(context: Context): File? {
        val base = context.getExternalFilesDir(null) ?: return null
        return File(base, "Assist")
    }

    fun requiredBytes(): Long = SIZE_BYTES

    /**
     * True when every asset is present and the copy came from the currently installed APK.
     *
     * Only file presence and length are checked, so this stays cheap enough to call on every
     * launch. Content is verified by the Python side, which has the hashes.
     */
    fun isInstalled(context: Context): Boolean {
        val dir = installDir(context) ?: return false

        val stamp = File(dir, STAMP_NAME)
        if (!stamp.isFile || stamp.readText().trim() != installToken(context)) {
            return false
        }

        val names = assetNames(context) ?: return false
        if (names.isEmpty()) {
            return false
        }

        return names.all { name -> File(dir, name).let { it.isFile && it.length() > 0L } }
    }

    /**
     * Makes sure the narration files exist as real files and returns their directory.
     *
     * Runs blocking I/O, so call it off the UI thread. [onProgress] receives a value from 0 to 100
     * and may be called from this thread.
     */
    @Throws(IOException::class)
    fun ensureInstalled(context: Context, onProgress: (Int) -> Unit): File {
        val dir = installDir(context) ?: throw IOException("External storage unavailable")
        if (!dir.isDirectory && !dir.mkdirs()) {
            throw IOException("Could not create ${dir.absolutePath}")
        }

        val names = assetNames(context) ?: throw IOException("Missing $ASSET_DIR assets")

        // The stamp is removed first, so an interrupted copy leaves something isInstalled rejects.
        val stamp = File(dir, STAMP_NAME)
        if (stamp.exists() && !stamp.delete()) {
            throw IOException("Could not replace ${stamp.absolutePath}")
        }

        Log.debug("[AssistAssets] Extracting ${names.size} files to ${dir.absolutePath}")

        val buffer = ByteArray(COPY_BUFFER_BYTES)
        for ((index, name) in names.withIndex()) {
            val target = File(dir, name)
            writeAsset(context, "$ASSET_DIR/$name", target, buffer)
            onProgress((index + 1) * 100 / names.size)
        }

        stamp.writeText(installToken(context))
        onProgress(100)
        return dir
    }

    /** Copies one asset through a temporary file, so a partial file is never left behind. */
    @Throws(IOException::class)
    private fun writeAsset(context: Context, assetPath: String, target: File, buffer: ByteArray) {
        val temp = File(target.parentFile, target.name + ".part")
        if (temp.exists() && !temp.delete()) {
            throw IOException("Could not replace ${temp.absolutePath}")
        }

        context.assets.open(assetPath).use { input ->
            temp.outputStream().use { output ->
                while (true) {
                    val read = input.read(buffer)
                    if (read <= 0) {
                        break
                    }
                    output.write(buffer, 0, read)
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
    }

    private fun assetNames(context: Context): List<String>? =
        context.assets.list(ASSET_DIR)?.filter { !it.endsWith(".part") }?.sorted()

    /**
     * Identifies the installed APK. Any update reinstalls the assets, because the stamp no longer
     * matches, and a first install has no stamp at all.
     *
     * The base APK's own size and timestamp are used rather than the package version, so this
     * stays correct for a build installed over another with the same version name.
     */
    private fun installToken(context: Context): String {
        val apk = File(context.packageCodePath)
        return "${apk.length()}:${apk.lastModified()}"
    }
}
