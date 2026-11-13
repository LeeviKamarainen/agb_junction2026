import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

val localProps = Properties().apply {
    val f = rootDir.resolve("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "com.agb.glassescam"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.agb.glassescam"
        minSdk = 31
        targetSdk = 36
        versionCode = 1
        versionName = "0.1"

        // "0" = Developer Mode (no Developer Center project needed).
        manifestPlaceholders["mwdat_application_id"] = localProps.getProperty("mwdat.applicationId", "0")
        manifestPlaceholders["mwdat_client_token"] = localProps.getProperty("mwdat.clientToken", "0")

        buildConfigField(
            "String",
            "BACKEND_URL",
            "\"${localProps.getProperty("backend.url", "")}\""
        )
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)

    implementation(libs.mwdat.core)
    implementation(libs.mwdat.camera)
    implementation(libs.mwdat.mockdevice) // "Mock glasses" button; uses the phone camera as the feed
}
