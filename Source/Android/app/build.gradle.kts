plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.androidx.baselineprofile)
    alias(libs.plugins.chaquopy)
}

@Suppress("UnstableApiUsage")
android {
    compileSdk = 37
    ndkVersion = "30.0.15729638"

    buildFeatures {
        compose = true
        viewBinding = true
        buildConfig = true
        resValues = true
    }

    compileOptions {
        // Flag to enable support for the new language APIs
        isCoreLibraryDesugaringEnabled = true

        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        // This is important as it will run lint but not abort on error
        // Lint has some overly obnoxious "errors" that should really be warnings
        abortOnError = false

        //Uncomment disable lines for test builds...
        //disable "MissingTranslation"
        //disable "ExtraTranslation"
    }

    defaultConfig {
        // This build is a single-game launcher, so it must be installable
        // alongside a regular Dolphin build. Only the application ID changes:
        // the Java package (= namespace) stays org.dolphinemu.dolphinemu, so
        // the JNI symbol names in Source/Android/jni keep matching.
        applicationId = "com.boz700908.minnanorhythm"
        minSdk = 24
        targetSdk = 37

        versionCode = 1

        versionName = "1.0"

        buildConfigField("String", "GIT_HASH", "\"${getGitHash()}\"")
        buildConfigField("String", "BRANCH", "\"${getBranch()}\"")

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    // A debug build is signed with a key Gradle generates on the machine that builds
    // it, so every CI run produced an APK that could not be installed over the last
    // one. CI passes the project's own key in instead, and that signs the debug build
    // too so the app is updated in place and keeps its save data.
    val stableKeystore = System.getenv("MINNAN_KEYSTORE")

    signingConfigs {
        create("release") {
            if (project.hasProperty("keystore")) {
                storeFile = file(project.property("keystore")!!)
                storePassword = project.property("storepass").toString()
                keyAlias = project.property("keyalias").toString()
                keyPassword = project.property("keypass").toString()
            }
        }

        if (stableKeystore != null && file(stableKeystore).exists()) {
            create("stable") {
                storeFile = file(stableKeystore)
                storePassword = System.getenv("MINNAN_STORE_PASSWORD")
                keyAlias = System.getenv("MINNAN_KEY_ALIAS") ?: "minnanorhythm"
                keyPassword = System.getenv("MINNAN_KEY_PASSWORD")
            }
        }
    }

    // Define build types, which are orthogonal to product flavors.
    buildTypes {
        // Signed by release key, allowing for upload to Play Store.
        release {
            if (project.hasProperty("keystore")) {
                signingConfig = signingConfigs.getByName("release")
            }

            resValue("string", "app_name_suffixed", "\u307f\u3093\u306a\u306e\u30ea\u30ba\u30e0\u5929\u56fd")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }

        // Signed by debug key disallowing distribution on Play Store.
        // No application ID or version suffix: this fork is a single-game
        // launcher and a debug build has to look like the final product, both
        // in the app list and in the version string.
        debug {
            resValue("string", "app_name_suffixed", "\u307f\u3093\u306a\u306e\u30ea\u30ba\u30e0\u5929\u56fd")
            isJniDebuggable = true

            signingConfigs.findByName("stable")?.let { signingConfig = it }
        }
    }

    externalNativeBuild {
        cmake {
            path = file("../../../CMakeLists.txt")
            version = "3.25.0+"
        }
    }
    namespace = "org.dolphinemu.dolphinemu"

    defaultConfig {
        ndk {
            // We only officially support arm64-v8a and x86_64.
            // If you want to try building other ABIs anyway, you need -DENABLE_GENERIC=ON.
            abiFilters += listOf("arm64-v8a", "x86_64") //, "armeabi-v7a", "x86"
        }

        externalNativeBuild {
            cmake {
                arguments(
                    "-DANDROID_STL=c++_static",
                    "-DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON",
                    "-DCMAKE_BUILD_TYPE=RelWithDebInfo"
                    // , "-DENABLE_GENERIC=ON"
                )

                // Uncomment the line below if you don't want to build the C++ unit tests
                //targets("main", "hook_impl", "main_hook", "gsl_alloc_hook", "file_redirect_hook")
            }
        }
    }

    packaging {
        jniLibs.useLegacyPackaging = true
    }
}

// The Chinese rhythm-game narration runs as Python inside this process, so it
// can read guest RAM directly instead of talking to a separate program.
chaquopy {
    defaultConfig {
        // Same minor version as the code was written and tested against.
        version = "3.12"

        // Only the standard library is used; no third-party packages are
        // installed, so no pip repositories are configured here.
        buildPython("python3")
    }
}

dependencies {
    baselineProfile(project(":benchmark"))
    coreLibraryDesugaring(libs.desugar.jdk.libs)

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.cardview)
    implementation(libs.androidx.recyclerview)
    implementation(libs.androidx.constraintlayout)
    implementation(libs.androidx.fragment.ktx)
    implementation(libs.androidx.slidingpanelayout)
    implementation(libs.material)
    implementation(libs.androidx.core.splashscreen)
    implementation(libs.androidx.preference.ktx)
    implementation(libs.androidx.profileinstaller)

    // Kotlin extensions for lifecycle components
    implementation(libs.androidx.lifecycle.livedata.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)

    // Android TV UI libraries.
    implementation(libs.androidx.leanback)
    implementation(libs.androidx.tvprovider)
    implementation(libs.androidx.swiperefreshlayout)

    // For loading game covers from disk and GameTDB
    implementation(libs.coil)
    implementation(libs.coil.compose)

    // For loading custom GPU drivers
    implementation(libs.kotlinx.serialization.json)

    implementation(libs.kotlinx.coroutines.android)

    implementation(libs.filepicker)

    // Jetpack Compose
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material.icons)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material3.adaptive)
    implementation(libs.androidx.compose.runtime.livedata)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling)
    implementation(libs.androidx.compose.ui.tooling.preview)

    implementation(libs.accompanist)
}

fun getGitHash(): String {
    try {
        return ProcessBuilder("git", "rev-parse", "HEAD")
            .directory(project.rootDir)
            .redirectOutput(ProcessBuilder.Redirect.PIPE)
            .redirectError(ProcessBuilder.Redirect.PIPE)
            .start().inputStream.bufferedReader().use { it.readText() }
            .trim()
    } catch (e: Exception) {
        logger.error("Cannot find git, defaulting to dummy git hash")
    }

    return "0"
}

fun getBranch(): String {
    try {
        return ProcessBuilder("git", "rev-parse", "--abbrev-ref", "HEAD")
            .directory(project.rootDir)
            .redirectOutput(ProcessBuilder.Redirect.PIPE)
            .redirectError(ProcessBuilder.Redirect.PIPE)
            .start().inputStream.bufferedReader().use { it.readText() }
            .trim()
    } catch (e: Exception) {
        logger.error("Cannot find git, defaulting to dummy git hash")
    }

    return "master"
}
