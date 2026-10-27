import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import org.jetbrains.kotlin.gradle.tasks.KotlinCompile

plugins {
    kotlin("jvm") version "2.4.21"
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
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.11.0")

    // HTTP client (OkHttp 4 — LTS, widely used, supports custom SocketFactory for UDS)
    implementation("com.squareup.okhttp3:okhttp:4.12.1")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.1")

    // JSON
    implementation("com.google.code.gson:gson:2.14.0")

    // OkIO (transitive via OkHttp, explicitly listed for clarity)
    implementation("com.squareup.okio:okio:3.18.2")

    // AWS SDK v2 auth — provides the native SigV4 signer (AwsV4HttpSigner / Aws4Signer)
    // and the standard credential provider chain. No AWS service stubs are pulled in;
    // only the auth/signer module and its minimal transitive dependencies are included.
    implementation("software.amazon.awssdk:auth:2.57.3")
    implementation("software.amazon.awssdk:regions:2.57.3")

    // Test
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.11.0")
    testImplementation("io.mockk:mockk:1.14.11")

    // JUnit versions come from the BOM, and junit-platform-launcher is declared
    // explicitly rather than left to Gradle's bundled copy: from JUnit 6 onwards
    // the engine will not load against the launcher Gradle injects by default,
    // which fails at test-discovery time ("TestEngine with ID 'junit-jupiter'
    // failed to discover tests"). Declaring it keeps a JUnit major bump buildable.
    testImplementation(platform("org.junit:junit-bom:5.14.4"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.1")
    testRuntimeOnly("org.junit.jupiter:junit-jupiter-engine")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

// ---------------------------------------------------------------------------
// Compile settings
// ---------------------------------------------------------------------------
// `compilerOptions` (not the older `kotlinOptions`) is the forward-compatible
// DSL: KGP 2.x annotates KotlinJvmOptions with @Deprecated(level = ERROR), so a
// `kotlinOptions` block stops the build script from compiling at CONFIGURATION
// time — before any task runs — the moment the Kotlin plugin crosses 2.0.
tasks.withType<KotlinCompile>().configureEach {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
        freeCompilerArgs.add("-Xjsr305=strict")
    }
}

// ---------------------------------------------------------------------------
// Unit tests (always run; contract tests excluded unless ORB_TEST=true)
// ---------------------------------------------------------------------------
tasks.test {
    useJUnitPlatform()
    // Contract + parity tests require a live ORB binary — opt-in via ORB_TEST=true
    if (System.getenv("ORB_TEST") != "true") {
        exclude("**/contract/**")
        exclude("**/parity/**")
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

// ---------------------------------------------------------------------------
// parityTest — dedicated task for the cross-language parity scenario.
// Loads sdk/parity/scenario.json and drives its six steps against a real ORB.
// Requires ORB_BINARY env var, like contractTest.
// ---------------------------------------------------------------------------
tasks.register<Test>("parityTest") {
    useJUnitPlatform()
    include("**/parity/**")
    systemProperty("junit.jupiter.execution.timeout.default", "120s")

    doFirst {
        val binary = System.getenv("ORB_BINARY")
        if (binary.isNullOrBlank()) {
            throw GradleException(
                "parityTest requires ORB_BINARY to be set in the environment.\n" +
                "Example: ORB_BINARY=/path/to/.venv/bin/python ./gradlew parityTest"
            )
        }
        println("parityTest: ORB_BINARY=$binary")
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
        maven {
            name = "GitHubPackages"
            // GitHub Packages Maven registry for finos/open-resource-broker.
            // Auth uses the CI GITHUB_TOKEN (packages: write) — no GPG required.
            url = uri("https://maven.pkg.github.com/finos/open-resource-broker")
            credentials {
                username = System.getenv("GITHUB_ACTOR") ?: findProperty("gprUser") as String?
                password = System.getenv("GITHUB_TOKEN")
                    ?: (findProperty("gprKey") ?: findProperty("githubToken")) as String?
            }
        }
    }
}

// Signs all Maven publications using the GPG key passed via -Psigning.gnupg.passphrase.
// Signing is conditional: it is only wired in when a GPG key/passphrase is present
// (Maven Central release). GitHub Packages accepts unsigned artifacts, so builds that
// publish only to GitHubPackages (auth via GITHUB_TOKEN, no GPG) do not fail here.
// NOTE: In CI the publish step declares MAVEN_GPG_PASSPHRASE via `${{ secrets.* }}`.
// When the secret is absent GitHub Actions sets the env var to the EMPTY STRING "" —
// not unset — so a bare `!= null` check is always true and would wire signing on the
// GitHub Packages (unsigned) path, breaking it. Require a NON-EMPTY passphrase instead.
val gpgPass = System.getenv("MAVEN_GPG_PASSPHRASE")
if (project.hasProperty("signing.gnupg.passphrase") || !gpgPass.isNullOrBlank()) {
    signing {
        // Use the gpg command-line signatory so the imported GPG key (and
        // -Psigning.gnupg.passphrase) is used; without useGpgCmd() gradle
        // expects an in-memory key and fails with "no configured signatory".
        useGpgCmd()
        sign(publishing.publications["maven"])
    }
}
