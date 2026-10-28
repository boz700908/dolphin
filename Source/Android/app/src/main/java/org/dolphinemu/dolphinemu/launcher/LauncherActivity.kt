// SPDX-License-Identifier: GPL-2.0-or-later

package org.dolphinemu.dolphinemu.launcher

import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.os.Bundle
import android.view.View
import android.widget.ImageView
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import org.dolphinemu.dolphinemu.R
import org.dolphinemu.dolphinemu.activities.EmulationActivity
import org.dolphinemu.dolphinemu.databinding.ActivityLauncherBinding
import org.dolphinemu.dolphinemu.dialogs.GamePropertiesDialog
import org.dolphinemu.dolphinemu.features.settings.model.BooleanSetting
import org.dolphinemu.dolphinemu.features.settings.model.Settings
import org.dolphinemu.dolphinemu.features.settings.ui.MenuTag
import org.dolphinemu.dolphinemu.features.settings.ui.SettingsActivity
import org.dolphinemu.dolphinemu.model.GameFile
import org.dolphinemu.dolphinemu.services.GameFileCacheManager
import org.dolphinemu.dolphinemu.ui.main.ThemeProvider
import org.dolphinemu.dolphinemu.utils.AfterDirectoryInitializationRunner
import org.dolphinemu.dolphinemu.utils.CoilUtils
import org.dolphinemu.dolphinemu.utils.DirectoryInitialization
import org.dolphinemu.dolphinemu.utils.Log
import org.dolphinemu.dolphinemu.utils.PermissionsHandler
import org.dolphinemu.dolphinemu.utils.ThemeHelper
import java.io.File
import java.io.IOException
import kotlin.concurrent.thread

/**
 * The whole front end of this build: one game, two buttons.
 *
 * Everything shown about the game - its title, its artwork, its game ID - comes from the
 * emulator's own disc parser, so this screen stays correct if the bundled game is replaced. Only
 * [BundledGame] knows which asset ships in the APK.
 *
 * The image is expanded out of the APK as soon as the app starts, so this screen can already show
 * the real title and artwork and the Launch button goes straight into emulation. The core can only
 * read a real file, which is why the copy is unavoidable.
 */
class LauncherActivity : AppCompatActivity(), ThemeProvider {
    override var themeId = 0

    private lateinit var binding: ActivityLauncherBinding

    /** True while the bundled game is being copied out of the APK or scanned afterwards. */
    private var preparing = false

    /** The parsed game, or null until the image exists and has been scanned. */
    private var gameFile: GameFile? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen().setKeepOnScreenCondition {
            !DirectoryInitialization.areDolphinDirectoriesReady()
        }

        ThemeHelper.setTheme(this)
        enableEdgeToEdge()

        super.onCreate(savedInstanceState)

        binding = ActivityLauncherBinding.inflate(layoutInflater)
        setContentView(binding.root)

        setInsets()

        // Until the parser has looked at the disc, show the app name and the no-banner artwork.
        binding.textTitle.setText(R.string.app_name_suffixed)
        binding.imageCover.setImageResource(R.drawable.no_banner)

        binding.buttonLaunch.setOnClickListener { launchGame() }

        // Long-pressing the title does what long-pressing a game does in the emulator's library:
        // it opens the game properties menu for this game.
        binding.textTitle.setOnLongClickListener { showGameProperties() }

        binding.buttonSettings.setOnClickListener {
            SettingsActivity.launch(this, MenuTag.SETTINGS)
        }

        if (savedInstanceState == null) {
            AfterDirectoryInitializationRunner().runWithLifecycle(this) {
                ThemeHelper.setCorrectTheme(this)
                // Expand the bundled game right away: the title and the artwork come from the
                // emulator's parser, so it has to be a real file before this screen can be filled
                // in properly.
                prepareGame()
            }
        } else {
            prepareGame()
        }
    }

    override fun onResume() {
        ThemeHelper.setCorrectTheme(this)
        super.onResume()

        if (DirectoryInitialization.shouldStart(this)) {
            DirectoryInitialization.start(this)
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == PermissionsHandler.REQUEST_CODE_WRITE_PERMISSION) {
            if (grantResults.isNotEmpty() &&
                grantResults[0] == PackageManager.PERMISSION_DENIED
            ) {
                PermissionsHandler.setWritePermissionDenied()
            }

            // The directories are created asynchronously; expanding the game before they exist
            // fails, so wait for the initialization the same way onCreate does.
            DirectoryInitialization.start(this)
            AfterDirectoryInitializationRunner().runWithLifecycle(this) { prepareGame() }
        }
    }

    override fun setTheme(themeId: Int) {
        super.setTheme(themeId)
        this.themeId = themeId
    }

    /**
     * Makes sure the bundled game is on disk and identified. Nothing happens if that work is
     * already running.
     */
    private fun prepareGame() {
        if (preparing) {
            return
        }

        if (BundledGame.isInstalled(this) && AssistAssets.isInstalled(this)) {
            refreshGameInfo()
        } else {
            installGame()
        }
    }

    /** Copies the game and the narration data out of the APK, then identifies the game. */
    private fun installGame() {
        if (DirectoryInitialization.isWaitingForWriteAccess(this)) {
            PermissionsHandler.requestWritePermission(this)
            return
        }

        // Both copies happen here, so both are counted against the free space.
        val missingBytes =
            (if (BundledGame.isInstalled(this)) 0L else BundledGame.requiredBytes()) +
                    (if (AssistAssets.isInstalled(this)) 0L else AssistAssets.requiredBytes())

        val dir = BundledGame.installDir(this)
        val freeBytes = dir?.let { nearestExistingParent(it).usableSpace } ?: 0L
        if (freeBytes in 1 until missingBytes) {
            val neededMb = ((missingBytes - freeBytes) / (1024 * 1024)).toInt() + 1
            MaterialAlertDialogBuilder(this)
                .setMessage(getString(R.string.launcher_not_enough_space, neededMb))
                .setPositiveButton(android.R.string.ok, null)
                .show()
            return
        }

        preparing = true
        setBusy(true)
        binding.textStatus.visibility = View.VISIBLE
        binding.textStatus.setText(R.string.launcher_preparing)

        thread(name = "bundled-game-install") {
            try {
                if (!BundledGame.isInstalled(this)) {
                    BundledGame.ensureInstalled(this) { percent ->
                        runOnUiThread {
                            binding.textStatus.text =
                                getString(R.string.launcher_installing_game, percent)
                        }
                    }
                }

                // The narration data is expanded in this same pass, so nothing later on has to
                // wait for a copy. It sits beside the game rather than inside the interpreter's
                // own assets because the reader needs real files it can hash and read at random.
                if (!AssistAssets.isInstalled(this)) {
                    AssistAssets.ensureInstalled(this) { percent ->
                        runOnUiThread {
                            binding.textStatus.text =
                                getString(R.string.launcher_installing_assist, percent)
                        }
                    }
                }

                // Identify the file with the emulator's parser on this same background thread, so
                // the UI only has to display the result.
                val game = scan(BundledGame.installedFile(this)!!.absolutePath)

                runOnUiThread {
                    preparing = false
                    setBusy(false)
                    binding.textStatus.visibility = View.GONE
                    if (isFinishing || isDestroyed) {
                        return@runOnUiThread
                    }
                    if (game != null) {
                        gameFile = game
                        showGame(game)
                    }
                }
            } catch (e: IOException) {
                runOnUiThread {
                    preparing = false
                    setBusy(false)
                    binding.textStatus.visibility = View.GONE
                    if (isFinishing || isDestroyed) {
                        return@runOnUiThread
                    }
                    MaterialAlertDialogBuilder(this)
                        .setMessage(
                            getString(
                                R.string.launcher_install_failed,
                                e.message ?: e.javaClass.simpleName
                            )
                        )
                        .setPositiveButton(android.R.string.ok, null)
                        .show()
                }
            }
        }
    }

    /**
     * Asks the emulator's own game cache to identify the bundled image and fills in the title and
     * the artwork from what it reports.
     */
    private fun refreshGameInfo() {
        val path = BundledGame.installedFile(this)?.absolutePath ?: return

        if (preparing) {
            return
        }
        preparing = true
        setBusy(true)
        binding.textStatus.visibility = View.VISIBLE
        binding.textStatus.setText(R.string.launcher_preparing)

        thread(name = "bundled-game-scan") {
            val game = scan(path)
            runOnUiThread {
                preparing = false
                setBusy(false)
                binding.textStatus.visibility = View.GONE
                if (isFinishing || isDestroyed) {
                    return@runOnUiThread
                }
                if (game != null) {
                    gameFile = game
                    showGame(game)
                }
            }
        }
    }

    /** Runs the emulator's own disc parser. Must be called off the main thread. */
    private fun scan(path: String): GameFile? {
        return try {
            // Reading the settings first is what makes the emulator's preferences (such as whether
            // it should use game covers at all) apply to what this screen shows.
            Settings().use { it.loadSettings() }
            GameFileCacheManager.addOrGet(path)
        } catch (e: Exception) {
            Log.error("Could not identify $path: ${e.message}")
            null
        }
    }

    /**
     * Opens the same game properties menu the emulator's library shows when a game is long-pressed.
     * Returns false while the game has not been identified yet, so the press falls through.
     */
    private fun showGameProperties(): Boolean {
        val game = gameFile ?: return false

        val fragment = GamePropertiesDialog.newInstance(game)
        supportFragmentManager.beginTransaction()
            .add(fragment, GamePropertiesDialog.TAG).commit()
        return true
    }

    private fun showGame(game: GameFile) {
        val title = game.getTitle()
        if (title.isNotEmpty()) {
            binding.textTitle.text = title
            binding.textTitleInner.text = title
        }

        // The grid can be set to draw the title over the artwork and nothing else, so follow it:
        // what this screen says about the game is whatever the grid would say about it.
        val showTitles = try {
            BooleanSetting.MAIN_SHOW_GAME_TITLES.boolean
        } catch (e: Exception) {
            true
        }

        binding.textTitle.visibility = if (showTitles) View.VISIBLE else View.GONE
        binding.textTitleInner.visibility = if (showTitles) View.GONE else View.VISIBLE
        binding.textCaption.visibility = if (showTitles) View.VISIBLE else View.GONE

        if (showTitles) {
            showGameCaption(game)
        }

        showGameArt(game)
    }

    /** The line the grid puts under a title: the company, or the disc number of a multi-disc game. */
    private fun showGameCaption(game: GameFile) {
        val caption = if (GameFileCacheManager.findSecondDisc(game) != null) {
            getString(R.string.disc_number, game.getDiscNumber() + 1)
        } else {
            game.getCompany()
        }

        binding.textCaption.text = caption
        binding.textCaption.visibility = if (caption.isEmpty()) View.GONE else View.VISIBLE
    }

    /**
     * Shows the artwork the emulator itself would show for this game: its own custom cover if one
     * exists, otherwise the same cover lookup the library uses (which honours the cover setting),
     * and failing that the banner stored inside the disc.
     */
    private fun showGameArt(game: GameFile) {
        binding.imageCover.scaleType = ImageView.ScaleType.FIT_CENTER

        val useCovers = try {
            BooleanSetting.MAIN_USE_GAME_COVERS.boolean
        } catch (e: Exception) {
            true
        }

        if (useCovers) {
            // The library's loader already falls back to error artwork and caches per game, so
            // reusing it keeps this screen consistent with the library.
            CoilUtils.loadGameCover(null, binding.imageCover, game)
            return
        }

        // Covers are switched off, so use the banner stored inside the disc, which the emulator
        // decodes for us.
        val width = game.getBannerWidth()
        val height = game.getBannerHeight()
        if (width <= 0 || height <= 0) {
            binding.imageCover.setImageResource(R.drawable.no_banner)
            return
        }

        try {
            val banner = game.getBanner()
            val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
            bitmap.setPixels(banner, 0, width, 0, 0, width, height)
            binding.imageCover.setImageBitmap(bitmap)
        } catch (e: Exception) {
            binding.imageCover.setImageResource(R.drawable.no_banner)
        }
    }

    /** Boots the bundled game exactly the way the emulator's own library boots a game. */
    private fun launchGame() {
        if (preparing) {
            return
        }

        if (DirectoryInitialization.isWaitingForWriteAccess(this)) {
            PermissionsHandler.requestWritePermission(this)
            return
        }

        val game = gameFile
        if (game != null) {
            startEmulation(game)
            return
        }

        // The file is there but was not identified (or not expanded yet); sort that out first.
        val path = BundledGame.installedFile(this)?.absolutePath
        if (path == null) {
            prepareGame()
            return
        }

        AfterDirectoryInitializationRunner().runWithLifecycle(this) {
            startEmulation(GameFileCacheManager.addOrGet(path))
        }
    }

    private fun startEmulation(game: GameFile) {
        val paths = GameFileCacheManager.findSecondDiscAndGetPaths(game)
        EmulationActivity.launch(this, paths, false)
    }

    private fun setBusy(busy: Boolean) {
        binding.buttonLaunch.isEnabled = !busy
        binding.buttonSettings.isEnabled = !busy
        binding.progressInstall.visibility = if (busy) View.VISIBLE else View.GONE
    }

    private fun nearestExistingParent(file: File): File {
        var current = file
        while (!current.exists() && current.parentFile != null) {
            current = current.parentFile!!
        }
        return current
    }

    private fun setInsets() =
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { view: View, windowInsets: WindowInsetsCompat ->
            val insets = windowInsets.getInsets(WindowInsetsCompat.Type.systemBars())
            val basePadding = resources.getDimensionPixelSize(R.dimen.spacing_large)
            view.setPadding(
                insets.left + basePadding,
                insets.top + basePadding,
                insets.right + basePadding,
                insets.bottom + basePadding
            )
            windowInsets
        }
}
