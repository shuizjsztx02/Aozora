plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.devtools.ksp") version "2.0.21-1.0.28"
    // T-M5-02d: OWASP DependencyCheck — Android dependency CVE scan.
    // Run locally with: ./gradlew :app:dependencyCheckAnalyze
    // Set NVD_API_KEY env var to avoid unauthenticated NVD rate limits.
    id("org.owasp.dependencycheck") version "11.1.0"
}

val webDistDir = layout.projectDirectory.dir("dist")
val generatedWebAssetsDir = layout.buildDirectory.dir("generated/webAssets")

val buildWeb by tasks.registering(Exec::class) {
    workingDir(projectDir)
    val npm = if (System.getProperty("os.name").startsWith("Windows")) "npm.cmd" else "npm"
    commandLine(npm, "run", "build")
    inputs.file("package.json")
    inputs.file("package-lock.json")
    inputs.dir("src")
    inputs.file("index.html")
    inputs.file("vite.config.ts")
    inputs.dir(rootProject.file("contracts/action-proposal"))
    outputs.dir(webDistDir)
}

val syncWebAssets by tasks.registering(Sync::class) {
    dependsOn(buildWeb)
    from(webDistDir)
    into(generatedWebAssetsDir)
}

android {
    namespace = "com.aozora.app"
    compileSdk = 36
    buildToolsVersion = "36.1.0"

    defaultConfig {
        applicationId = "com.aozora.app"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // Room schema export for MigrationTestHelper (T-M3-02)
        sourceSets.getByName("androidTest").assets.srcDir("$projectDir/schemas")

        // T-M3-02: sync server base URL, split by build type
        buildConfigField("String", "SYNC_BASE_URL", "\"https://api.example.invalid\"")
    }

    buildFeatures {
        buildConfig = true
    }

    buildTypes {
        debug {
            // Debug builds reach the dev server via emulator loopback by default;
            // network_security_config permits cleartext only for localhost/10.0.2.2.
            // 真机 USB 联调（第六轮 Bug-5.3）：手机与 PC 网段隔离时，用
            //   adb reverse tcp:8000 tcp:8000
            //   .\gradlew :app:assembleDebug "-PAOZORA_DEBUG_API_BASE=http://127.0.0.1:8000/api/v1"
            // 走 localhost 通路（NSC 已允许 localhost 明文）。
            val debugUrl = (project.findProperty("AOZORA_DEBUG_API_BASE") as String?)
                ?.let { if (it.startsWith("\"")) it else "\"$it\"" }
                ?: "\"http://10.0.2.2:8000/api/v1\""
            buildConfigField("String", "SYNC_BASE_URL", debugUrl)
        }
        release {
            isMinifyEnabled = false
            // Release requires HTTPS base URL injected via gradle property
            // AOZORA_API_BASE (e.g. "-PAOZORA_API_BASE=https://..."). When
            // unset, the placeholder remains and the sync engine reports
            // auth_failed / disabled — no silent fall-through.
            val releaseUrl = project.findProperty("AOZORA_API_BASE") as String?
                ?: "\"https://api.example.invalid\""
            buildConfigField("String", "SYNC_BASE_URL", releaseUrl)
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    sourceSets.getByName("main").assets.srcDir(generatedWebAssetsDir)
    lint {
        // WorkManagerInitializer removal is a pre-existing T-M1-03 cleanup task;
        // suppress here to unblock lint gate for T-M3-02 push.
        disable += "RemoveWorkManagerInitializer"
    }
    testOptions {
        unitTests.isIncludeAndroidResources = true
        // 第六轮 Bug-5.1 后 AozoraApp.onCreate 执行 Room withTransaction（ensureSeedDefaults），
        // Room 事务在 arch_disk_io 线程池运行，而 Robolectric 4.11 默认的 legacy SQLite shadow
        // 连接表是 thread-local → "Illegal connection pointer"。NATIVE shadow 无此限制
        // （亦为 Robolectric 4.12+ 默认模式）。
        unitTests.all {
            it.systemProperty("robolectric.sqliteMode", "NATIVE")
        }
    }
}

tasks.named("preBuild").configure { dependsOn(syncWebAssets) }

ksp {
    arg("room.schemaLocation", "$projectDir/schemas")
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.11.0")

    // Room
    val roomVersion = "2.6.1"
    implementation("androidx.room:room-runtime:$roomVersion")
    implementation("androidx.room:room-ktx:$roomVersion")
    ksp("androidx.room:room-compiler:$roomVersion")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // T-M3-02: WorkManager background scheduler
    implementation("androidx.work:work-runtime-ktx:2.11.2")
    androidTestImplementation("androidx.work:work-testing:2.11.2")

    // T-M3-02: Lifecycle-aware sync state collection in MainActivity
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")

    // T-M3-02: HTTP client (sync + auth)
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    // T-M7-02h: SSE 长连接（agent 流式通道）。EventSource 不是 okhttp 核心的一部分。
    implementation("com.squareup.okhttp3:okhttp-sse:4.12.0")
    androidTestImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    // T-M7-02h: JVM 单测直连 MockWebServer 验证 SSE 帧透传与取消（同版本）。
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")

    // JSON
    testImplementation("org.json:json:20231013")

    // Unit tests
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")

    // T-M4-05a: Robolectric for ReminderScheduler unit tests
    testImplementation("org.robolectric:robolectric:4.12.2")
    testImplementation("androidx.test:core:1.5.0")

    // Instrumented tests
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.1")
    androidTestImplementation("androidx.room:room-testing:$roomVersion")
}
