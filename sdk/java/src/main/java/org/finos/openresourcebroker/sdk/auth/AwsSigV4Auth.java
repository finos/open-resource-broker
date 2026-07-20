// Layer 4: AWS SigV4 Authentication
package org.finos.openresourcebroker.sdk.auth;

import software.amazon.awssdk.auth.credentials.AwsCredentials;
import software.amazon.awssdk.auth.credentials.AwsCredentialsProvider;
import software.amazon.awssdk.auth.credentials.DefaultCredentialsProvider;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.AwsSessionCredentials;
import software.amazon.awssdk.http.ContentStreamProvider;
import software.amazon.awssdk.http.SdkHttpFullRequest;
import software.amazon.awssdk.http.SdkHttpMethod;
import software.amazon.awssdk.http.auth.aws.signer.AwsV4HttpSigner;
import software.amazon.awssdk.http.auth.spi.signer.SignedRequest;
import software.amazon.awssdk.http.auth.spi.signer.SignRequest;
import software.amazon.awssdk.regions.Region;

import java.io.ByteArrayInputStream;
import java.net.URI;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * AWS SigV4 authentication using the AWS SDK for Java v2.
 *
 * <p>Uses {@link AwsV4HttpSigner} from {@code software.amazon.awssdk:http-auth-aws} —
 * the current non-deprecated signer API.  Supports static credentials and the
 * default credential chain (env vars, {@code ~/.aws/credentials}, instance metadata).
 *
 * <p>The service name defaults to {@code "execute-api"} for API Gateway;
 * override for other services (e.g. {@code "execute-api"}, {@code "iam"}).
 *
 * <p>{@link #apply(Map)} is intentionally a no-op because SigV4 headers depend on
 * the full request URI and body, which are only known at send time.  Real signing is
 * performed per-request by {@link #signRequest}, which is called directly by
 * {@link org.finos.openresourcebroker.sdk.transport.RawHttpClient} for every outgoing
 * request including SSE streams.
 */
public class AwsSigV4Auth implements AuthStrategy {

    private final AwsCredentialsProvider credentialsProvider;
    private final Region region;
    private final String service;

    private static final AwsV4HttpSigner SIGNER = AwsV4HttpSigner.create();

    /** Use the AWS default credential chain. */
    public AwsSigV4Auth(String region, String service) {
        this.credentialsProvider = DefaultCredentialsProvider.create();
        this.region = Region.of(region);
        this.service = service != null ? service : "execute-api";
    }

    /** Use static credentials (with optional session token for temporary credentials). */
    public AwsSigV4Auth(String accessKeyId, String secretAccessKey, String sessionToken,
                         String region, String service) {
        AwsCredentials creds = sessionToken != null
                ? AwsSessionCredentials.create(accessKeyId, secretAccessKey, sessionToken)
                : AwsBasicCredentials.create(accessKeyId, secretAccessKey);
        this.credentialsProvider = StaticCredentialsProvider.create(creds);
        this.region = Region.of(region);
        this.service = service != null ? service : "execute-api";
    }

    /**
     * No-op: per-request signing is performed by {@link #signRequest}, which the
     * transport layer calls for every outgoing HTTP request.  Adding a static marker
     * header here would be wrong because SigV4 must sign the final headers/body.
     */
    @Override
    public void apply(Map<String, String> headers) {
        // Full SigV4 signing is wired per-request via signRequest().
    }

    /**
     * Sign a request and return the SigV4 headers (Authorization, x-amz-date,
     * x-amz-security-token) that must be added to the outgoing request.
     *
     * <p>Uses {@link AwsV4HttpSigner} — the non-deprecated AWS SDK v2 signer.
     *
     * @param method HTTP method (GET, POST, PUT, DELETE)
     * @param uri    full URI (including host, path, query)
     * @param body   request body bytes (may be null or empty for GET/DELETE)
     * @return map of signed headers to add to the request
     */
    public Map<String, String> signRequest(String method, URI uri, byte[] body) {
        try {
            AwsCredentials creds = credentialsProvider.resolveCredentials();

            SdkHttpFullRequest.Builder requestBuilder = SdkHttpFullRequest.builder()
                    .method(SdkHttpMethod.fromValue(method))
                    .uri(uri)
                    .putHeader("host", uri.getHost());

            ContentStreamProvider payload = null;
            if (body != null && body.length > 0) {
                final byte[] bodySnapshot = body;
                payload = () -> new ByteArrayInputStream(bodySnapshot);
                requestBuilder.contentStreamProvider(payload);
            }

            SignRequest.Builder<AwsCredentials> signReqBuilder =
                    SignRequest.builder(creds)
                               .request(requestBuilder.build())
                               .putProperty(AwsV4HttpSigner.SERVICE_SIGNING_NAME, service)
                               .putProperty(AwsV4HttpSigner.REGION_NAME, region.id());

            if (payload != null) {
                signReqBuilder.payload(payload);
            }

            SignedRequest signed = SIGNER.sign(signReqBuilder.build());

            // Return only the headers added by the signer (Authorization, x-amz-*)
            Map<String, String> result = new LinkedHashMap<>();
            signed.request().headers().forEach((k, v) -> {
                if (!v.isEmpty()) {
                    String lk = k.toLowerCase(java.util.Locale.ROOT);
                    if (lk.equals("authorization") || lk.startsWith("x-amz-")) {
                        result.put(k, v.get(0));
                    }
                }
            });
            return result;
        } catch (Exception e) {
            throw new RuntimeException("SigV4 signing failed: " + e.getMessage(), e);
        }
    }

    public boolean isSigV4() {
        return true;
    }
}
