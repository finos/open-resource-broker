/**
 * Layer 4: Authentication Interceptors
 *
 * Two auth modes:
 *   - Bearer token: adds "Authorization: Bearer <token>" header
 *   - AWS SigV4: signs requests using the native AWS SDK v2 signer
 *     (software.amazon.awssdk:auth — Aws4Signer + DefaultCredentialsProvider)
 *
 * Both are implemented as OkHttp Interceptors applied to the OkHttpClient.
 *
 * SigV4 reads credentials from the standard AWS credential chain via
 * DefaultCredentialsProvider (env vars → system properties → ~/.aws/credentials
 * → IAM instance profile). Explicit credentials override the chain when provided.
 *
 * Service defaults to "execute-api" for API Gateway-fronted deployments.
 */

package org.finos.openresourcebroker.sdk.auth

import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okio.Buffer
import org.finos.openresourcebroker.sdk.client.OrbError
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials
import software.amazon.awssdk.auth.credentials.AwsCredentialsProvider
import software.amazon.awssdk.auth.credentials.AwsSessionCredentials
import software.amazon.awssdk.auth.credentials.DefaultCredentialsProvider
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider
import software.amazon.awssdk.auth.signer.Aws4Signer
import software.amazon.awssdk.auth.signer.params.Aws4SignerParams
import software.amazon.awssdk.core.interceptor.ExecutionAttributes
import software.amazon.awssdk.http.SdkHttpFullRequest
import software.amazon.awssdk.http.SdkHttpMethod
import software.amazon.awssdk.regions.Region
import java.io.ByteArrayInputStream
import java.net.URI

/**
 * Authentication option sealed class.
 *
 * Three built-in strategies are provided: None, Bearer, and SigV4.
 * Use [Custom] as an escape hatch for auth strategies not covered here —
 * for example Azure Workload Identity, GCP service-account tokens, or OIDC
 * exchange flows.
 *
 * Example — Azure Managed Identity:
 *
 * ```kotlin
 * val azureAuth = AuthOption.Custom { chain ->
 *     val token = azureTokenProvider.getToken()
 *     chain.proceed(
 *         chain.request().newBuilder()
 *             .header("Authorization", "Bearer $token")
 *             .build()
 *     )
 * }
 * ```
 */
sealed class AuthOption {
    object None : AuthOption()

    /**
     * Static or dynamic Bearer token.
     *
     * Use [Bearer.of] with a lambda for a rotating token — the provider is
     * invoked on EVERY request, so token refresh takes effect immediately
     * (matching the Go/TypeScript/C# SDKs). A plain string is a constant token.
     */
    class Bearer private constructor(val tokenProvider: () -> String) : AuthOption() {
        constructor(token: String) : this({ token })

        companion object {
            /** Build a Bearer auth that invokes [provider] on every request. */
            fun of(provider: () -> String): Bearer = Bearer(provider)
        }
    }

    data class SigV4(
        val region: String,
        val service: String = "execute-api",
        /** If empty, uses the standard AWS credential chain (env vars → ~/.aws/credentials → IMDS) */
        val accessKeyId: String = "",
        val secretAccessKey: String = "",
        val sessionToken: String = "",
    ) : AuthOption()

    /**
     * Escape hatch: wrap any OkHttp [Interceptor] as an auth strategy.
     * The [interceptor] receives full control over the outbound request and
     * must call [Interceptor.Chain.proceed] exactly once.
     */
    data class Custom(val interceptor: Interceptor) : AuthOption()
}

/**
 * Factory — creates the correct OkHttp Interceptor for the given AuthOption.
 */
fun buildAuthInterceptor(auth: AuthOption): Interceptor? = when (auth) {
    is AuthOption.None -> null
    is AuthOption.Bearer -> BearerInterceptor(auth.tokenProvider)
    is AuthOption.SigV4 -> SigV4Interceptor(auth)
    is AuthOption.Custom -> auth.interceptor
}

// ---------------------------------------------------------------------------
// Bearer
// ---------------------------------------------------------------------------

/**
 * Adds `Authorization: Bearer <token>` to every request. The [tokenProvider] is
 * invoked on each request so a rotating token is always current.
 */
class BearerInterceptor(private val tokenProvider: () -> String) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val req = chain.request().newBuilder()
            .header("Authorization", "Bearer ${tokenProvider()}")
            .build()
        return chain.proceed(req)
    }
}

// ---------------------------------------------------------------------------
// AWS SigV4 — native AWS SDK v2 signer (software.amazon.awssdk:auth)
// ---------------------------------------------------------------------------

/**
 * SigV4 interceptor backed by the native AWS SDK v2 [Aws4Signer].
 *
 * Signs every outbound request using AWS Signature Version 4, adding the
 * standard Authorization, x-amz-date and (if using session credentials)
 * x-amz-security-token headers. Applies to all HTTP methods, including
 * requests that open SSE streams.
 */
class SigV4Interceptor(private val cfg: AuthOption.SigV4) : Interceptor {

    private val signer: Aws4Signer = Aws4Signer.create()

    private val credentialsProvider: AwsCredentialsProvider = when {
        cfg.accessKeyId.isNotEmpty() -> {
            // Explicit credentials supplied — wrap in a static provider
            val creds = if (cfg.sessionToken.isNotEmpty()) {
                AwsSessionCredentials.create(cfg.accessKeyId, cfg.secretAccessKey, cfg.sessionToken)
            } else {
                AwsBasicCredentials.create(cfg.accessKeyId, cfg.secretAccessKey)
            }
            StaticCredentialsProvider.create(creds)
        }
        else -> DefaultCredentialsProvider.create()
    }

    override fun intercept(chain: Interceptor.Chain): Response {
        val okRequest = chain.request()

        // Resolve credentials. If the credential chain cannot produce credentials
        // we fail loud rather than sending an unsigned request: silently emitting
        // an unauthenticated request masks a misconfiguration and diverges from the
        // other SDKs, which surface credential-resolution failure as an error.
        val awsCreds = try {
            credentialsProvider.resolveCredentials()
        } catch (e: Exception) {
            throw OrbError(
                "SigV4 auth failed: could not resolve AWS credentials from the " +
                    "credential chain (env vars → system properties → ~/.aws/credentials " +
                    "→ IAM instance profile): ${e.message}",
                e,
            )
        }

        // Read the body bytes so we can both sign and re-send them
        val bodyBytes: ByteArray = okRequest.body?.let { body ->
            val buf = Buffer()
            body.writeTo(buf)
            buf.readByteArray()
        } ?: ByteArray(0)

        // Build an SdkHttpFullRequest that mirrors the OkHttp request
        val uri = URI.create(okRequest.url.toString())

        val sdkRequestBuilder = SdkHttpFullRequest.builder()
            .method(SdkHttpMethod.fromValue(okRequest.method))
            .uri(uri)
            .apply {
                // Copy all existing headers (except Host which is implicit)
                okRequest.headers.names().forEach { name ->
                    if (!name.equals("host", ignoreCase = true)) {
                        okRequest.headers(name).forEach { value ->
                            appendHeader(name, value)
                        }
                    }
                }
                // Body content for signing
                if (bodyBytes.isNotEmpty()) {
                    contentStreamProvider { ByteArrayInputStream(bodyBytes) }
                }
            }

        val signerParams = Aws4SignerParams.builder()
            .awsCredentials(awsCreds)
            .signingName(cfg.service)
            .signingRegion(Region.of(cfg.region))
            .build()

        // Sign — the signer returns a new SdkHttpFullRequest with auth headers added
        val signedSdkRequest = signer.sign(sdkRequestBuilder.build(), signerParams)

        // Transfer the signed headers back onto the OkHttp request
        val newOkRequestBuilder = okRequest.newBuilder()
        signedSdkRequest.headers().forEach { (name, values) ->
            // Replace (not append) to avoid duplicates if the header was already present
            newOkRequestBuilder.removeHeader(name)
            values.forEach { value -> newOkRequestBuilder.addHeader(name, value) }
        }

        // Re-attach the body (the original body stream may have been consumed)
        if (bodyBytes.isNotEmpty() && okRequest.body != null) {
            val ct = okRequest.body!!.contentType()
            newOkRequestBuilder.method(okRequest.method, bodyBytes.toRequestBody(ct))
        }

        return chain.proceed(newOkRequestBuilder.build())
    }
}
