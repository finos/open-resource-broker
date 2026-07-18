import org.jetbrains.kotlin.gradle.tasks.KotlinCompile

plugins {
    kotlin("jvm") version "1.9.25"
    `java-library`
    `maven-publish`
    signing
}

group = "org.finos.openresourcebroker"
version = "0.1.0"

repositories {
    mavenCentral()
}

// ---------------------------------------------------------------------------
// Source sets: main = generated/src/main + src/main; test = src/test
// ---------------------------------------------------------------------------
sourceSets {
    main {
        kotlin {
            // Generated models + infrastructure
            srcDir("generated/src/main/kotlin")
            // Hand-written layers
            srcDir("src/main/kotlin")
        }
    }
    test {
        kotlin {
            srcDir("src/test/kotlin")
        }
    }
}

// ---------------------------------------------------------------------------
// Dependencies
// ---------------------------------------------------------------------------
dependencies {
    // Kotlin
    implementation(kotlin("stdlib"))
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.8.1")

    // HTTP client (OkHttp 4 — LTS, widely used, supports custom SocketFactory for UDS)
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")

    // JSON
    implementation("com.google.code.gson:gson:2.10.1")

    // OkIO (transitive via OkHttp, explicitly listed for clarity)
    implementation("com.squareup.okio:okio:3.9.0")

    // AWS SDK v2 auth — provides the native SigV4 signer (AwsV4HttpSigner / Aws4Signer)
    // and the standard credential provider chain. No AWS service stubs are pulled in;
    // only the auth/signer module and its minimal transitive dependencies are included.
    implementation("software.amazon.awssdk:auth:2.25.60")
    implementation("software.amazon.awssdk:regions:2.25.60")

    // Test
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
    testImplementation("io.mockk:mockk:1.13.11")
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.3")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    testRuntimeOnly("org.junit.jupiter:junit-jupiter-engine:5.10.3")
}

// ---------------------------------------------------------------------------
// Compile settings
// ---------------------------------------------------------------------------
tasks.withType<KotlinCompile> {
    kotlinOptions {
        jvmTarget = "17"
        freeCompilerArgs = listOf("-Xjsr305=strict")
    }
}

// ---------------------------------------------------------------------------
// Unit tests (always run; contract tests excluded unless ORB_TEST=true)
// ---------------------------------------------------------------------------
tasks.test {
    useJUnitPlatform()
    // Contract tests require a live ORB binary — opt-in via ORB_TEST=true
    if (System.getenv("ORB_TEST") != "true") {
        exclude("**/contract/**")
    }
}

// ---------------------------------------------------------------------------
// contractTest — dedicated task for the live contract suite.
// Requires ORB_BINARY env var (verified in ContractTest companion object init).
// Fails loudly rather than silently skipping when the env var is absent.
// ---------------------------------------------------------------------------
tasks.register<Test>("contractTest") {
    useJUnitPlatform()
    include("**/contract/**")
    // Long timeout for orb startup + all operations
    systemProperty("junit.jupiter.execution.timeout.default", "120s")

    doFirst {
        val binary = System.getenv("ORB_BINARY")
        if (binary.isNullOrBlank()) {
            throw GradleException(
                "contractTest requires ORB_BINARY to be set in the environment.\n" +
                "Example: ORB_BINARY=/path/to/.venv/bin/python ./gradlew contractTest"
            )
        }
        println("contractTest: ORB_BINARY=$binary")
    }
}

java {
    toolchain {
        languageVersion.set(JavaLanguageVersion.of(17))
    }
    withSourcesJar()
    withJavadocJar()
}

publishing {
    publications {
        create<MavenPublication>("maven") {
            groupId = "org.finos.openresourcebroker"
            // Aligns with the sibling JVM artifact 'open-resource-broker-java' so a
            // user who found one can guess the other. Coordinates are permanent once
            // published, so converge before the first release.
            artifactId = "open-resource-broker-kotlin"
            // version intentionally omitted — inherits project.version (line 10),
            // which the CI sed patches to the release tag before ./gradlew publish
            from(components["java"])
            pom {
                name.set("Open Resource Broker Kotlin SDK")
                description.set("Kotlin client SDK for the FINOS Open Resource Broker API")
                url.set("https://github.com/finos/open-resource-broker")
                licenses {
                    license {
                        name.set("Apache License, Version 2.0")
                        url.set("https://www.apache.org/licenses/LICENSE-2.0")
                    }
                }
                developers {
                    developer {
                        id.set("finos")
                        name.set("FINOS ORB Maintainers")
                        email.set("orb-maintainers@finos.org")
                    }
                }
                scm {
                    connection.set("scm:git:git://github.com/finos/open-resource-broker.git")
                    developerConnection.set("scm:git:ssh://github.com/finos/open-resource-broker.git")
                    url.set("https://github.com/finos/open-resource-broker")
                }
            }
        }
    }
    repositories {
        maven {
            name = "MavenCentral"
            // Central Portal OSSRH-compatible staging bridge.  The legacy
            // s01.oss.sonatype.org host was sunset by Sonatype; org.finos is
            // registered on the Central Portal, which exposes this staging API
            // for Gradle's maven-publish plugin.
            // See: https://central.sonatype.org/publish/publish-portal-ossrh-staging-api/
            url = uri("https://ossrh-staging-api.central.sonatype.com/service/local/staging/deploy/maven2/")
            credentials {
                username = findProperty("mavenCentralUsername") as String?
                password = findProperty("mavenCentralPassword") as String?
            }
        }
    }
}

// Signs all Maven publications using the GPG key passed via -Psigning.gnupg.passphrase.
// When the signing key is absent (e.g. local dev builds) signing is skipped automatically
// because required() is not set.
signing {
    sign(publishing.publications["maven"])
}
