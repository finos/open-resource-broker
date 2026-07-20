// Layer 4: Authentication
//
// Supports:
//   - None (no-op)
//   - Bearer token (static string or dynamic Func)
//   - AWS SigV4 using AWSSDK.Core AWS4Signer.ComputeSignature (native AWS SDK signing)
//
// SigV4 approach: AWS4Signer.ComputeSignature (public API in AWSSDK.Core) is used to
// compute the HMAC-SHA256 signing key derivation chain and produce the Authorization
// header value.  We build the canonical request (URI, headers, payload hash) from the
// HttpRequestMessage, then hand the canonical request + credentials to the AWS SDK's own
// ComputeSignature method — no hand-rolled HMAC code remains in this file.
//
// Auth is applied as a DelegatingHandler so it runs for EVERY request
// including SSE requests.

using System.Net.Http.Headers;
using Amazon.Runtime;
using Amazon.Runtime.Internal.Auth;

namespace FINOS.OpenResourceBroker.Auth;

// ---------------------------------------------------------------------------
// Auth option discriminated union
// ---------------------------------------------------------------------------

/// <summary>
/// Authentication strategy for OrbClient.
/// <para>
/// Three built-in strategies are provided: <see cref="None"/>, <see cref="Bearer"/>,
/// and <see cref="SigV4"/>. Use <see cref="Custom"/> as an escape hatch for auth
/// strategies not covered here — for example Azure Workload Identity, GCP
/// service-account tokens, or OIDC exchange flows.
/// </para>
/// <example>
/// Azure Managed Identity:
/// <code>
/// var azureAuth = AuthOption.Custom(async (request, ct) =>
/// {
///     var token = await azureTokenProvider.GetTokenAsync(ct);
///     request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
/// });
/// </code>
/// </example>
/// </summary>
public abstract class AuthOption
{
    private AuthOption() { }

    /// <summary>No authentication.</summary>
    public static readonly AuthOption None = new NoneAuth();

    internal sealed class NoneAuth : AuthOption { }
    internal sealed class BearerAuth : AuthOption
    {
        internal Func<CancellationToken, Task<string>> TokenProvider { get; }
        internal BearerAuth(Func<CancellationToken, Task<string>> provider) => TokenProvider = provider;
    }
    internal sealed class SigV4Auth : AuthOption
    {
        internal string Region { get; }
        internal string Service { get; }
        internal AWSCredentials Credentials { get; }
        internal SigV4Auth(string region, AWSCredentials credentials, string service)
        {
            Region = region; Service = service; Credentials = credentials;
        }
    }
    internal sealed class CustomAuth : AuthOption
    {
        internal Func<HttpRequestMessage, CancellationToken, Task> Signer { get; }
        internal CustomAuth(Func<HttpRequestMessage, CancellationToken, Task> signer) => Signer = signer;
    }

    /// <summary>Create Bearer token auth.</summary>
    public static AuthOption Bearer(string token) =>
        new BearerAuth(_ => Task.FromResult(token));

    /// <summary>Create Bearer token auth with a dynamic provider.</summary>
    public static AuthOption Bearer(Func<string> provider) =>
        new BearerAuth(_ => Task.FromResult(provider()));

    /// <summary>Create Bearer token auth with an async dynamic provider.</summary>
    public static AuthOption Bearer(Func<CancellationToken, Task<string>> provider) =>
        new BearerAuth(provider);

    /// <summary>Create AWS SigV4 auth.</summary>
    public static AuthOption SigV4(string region, AWSCredentials credentials, string service = "execute-api") =>
        new SigV4Auth(region, credentials, service);

    /// <summary>Create AWS SigV4 auth from the standard environment credential chain.</summary>
    public static AuthOption SigV4FromEnvironment(string? region = null, string service = "execute-api") =>
        new SigV4Auth(
            region ?? Environment.GetEnvironmentVariable("AWS_REGION")
                   ?? Environment.GetEnvironmentVariable("AWS_DEFAULT_REGION")
                   ?? "us-east-1",
            new EnvironmentVariablesAWSCredentials(),
            service);

    /// <summary>
    /// Create a custom auth strategy from an arbitrary signing delegate.
    /// <paramref name="signer"/> receives the outgoing <see cref="HttpRequestMessage"/>
    /// and may mutate its headers freely (e.g. set <c>Authorization</c> or
    /// provider-specific headers).
    /// </summary>
    public static AuthOption Custom(Func<HttpRequestMessage, CancellationToken, Task> signer) =>
        new CustomAuth(signer);
}

// ---------------------------------------------------------------------------
// DelegatingHandler that applies auth
// ---------------------------------------------------------------------------

/// <summary>
/// HttpMessageHandler that injects authentication headers into every request.
/// </summary>
internal sealed class AuthDelegatingHandler : DelegatingHandler
{
    private readonly AuthOption _auth;

    public AuthDelegatingHandler(AuthOption auth, HttpMessageHandler inner) : base(inner)
        => _auth = auth;

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken)
    {
        switch (_auth)
        {
            case AuthOption.NoneAuth:
                break;

            case AuthOption.BearerAuth bearer:
                var token = await bearer.TokenProvider(cancellationToken).ConfigureAwait(false);
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
                break;

            case AuthOption.SigV4Auth sigv4:
                await ApplySigV4Async(request, sigv4, cancellationToken).ConfigureAwait(false);
                break;

            case AuthOption.CustomAuth custom:
                await custom.Signer(request, cancellationToken).ConfigureAwait(false);
                break;
        }

        return await base.SendAsync(request, cancellationToken).ConfigureAwait(false);
    }

    // ---------------------------------------------------------------------------
    // AWS SigV4 signing using AWSSDK.Core's AWS4Signer.ComputeSignature
    //
    // We build the canonical request (method, URI, headers, payload hash) from the
    // HttpRequestMessage, then delegate ALL cryptographic work — the signing-key
    // derivation chain (HMAC-SHA256 rounds) and the final signature — to
    // AWS4Signer.ComputeSignature, which is a public method on the AWSSDK.Core type
    // Amazon.Runtime.Internal.Auth.AWS4Signer.
    //
    // AWS4Signer.ComputeSignature signature:
    //   AWS4SigningResult ComputeSignature(
    //       ImmutableCredentials credentials,
    //       string region, DateTime signedAt, string service,
    //       string signedHeaders, string canonicalRequest)
    //
    // The returned AWS4SigningResult.ForAuthorizationHeader property contains the
    // complete "AWS4-HMAC-SHA256 Credential=…, SignedHeaders=…, Signature=…" string
    // ready to drop into the Authorization header.
    //
    // No HMAC or SHA-256 code exists in this file — all crypto is in AWSSDK.Core.
    // ---------------------------------------------------------------------------

    private static async Task ApplySigV4Async(
        HttpRequestMessage request,
        AuthOption.SigV4Auth auth,
        CancellationToken ct)
    {
        var immutable = await auth.Credentials.GetCredentialsAsync().ConfigureAwait(false);

        var now = DateTime.UtcNow;
        var amzDate = now.ToString("yyyyMMddTHHmmssZ");

        var uri = request.RequestUri!;
        var host = uri.Host + (uri.IsDefaultPort ? "" : $":{uri.Port}");
        var method = request.Method.Method.ToUpperInvariant();

        // Compute payload hash (SHA-256 via AWS SDK helper)
        byte[] bodyBytes = Array.Empty<byte>();
        if (request.Content != null)
            bodyBytes = await request.Content.ReadAsByteArrayAsync(ct).ConfigureAwait(false);
        var payloadHash = Convert.ToHexString(AWS4Signer.ComputeHash(bodyBytes)).ToLowerInvariant();

        // Build sorted canonical headers
        var headersToSign = new SortedDictionary<string, string>(StringComparer.Ordinal)
        {
            ["host"] = host,
            ["x-amz-content-sha256"] = payloadHash,
            ["x-amz-date"] = amzDate,
        };
        if (!string.IsNullOrEmpty(immutable.Token))
            headersToSign["x-amz-security-token"] = immutable.Token;

        var canonicalHeadersStr = string.Join("\n", headersToSign.Select(kv => $"{kv.Key}:{kv.Value}")) + "\n";
        var signedHeadersStr = string.Join(";", headersToSign.Keys);

        // Canonical query string (RFC 3986, encoded exactly once, sorted).
        //
        // uri.Query is ALREADY percent-encoded (that is what goes on the wire), so
        // we must NOT run Uri.EscapeDataString over it again — doing so would
        // double-encode the canonical query (e.g. a wire "%20" becomes "%2520"),
        // making the signed canonical request diverge from what the server
        // recomputes → signature mismatch → 403.
        //
        // Instead we DECODE each key/value back to its raw form, then re-encode
        // each exactly once with RFC 3986 rules (Uri.EscapeDataString, which
        // percent-encodes everything except the unreserved set A-Z a-z 0-9 - _ . ~).
        // This matches the Go interceptor's encode()/isUnreserved() in
        // sdk/go/internal/transport/sigv4.go so every SDK produces the identical
        // canonical query string.  Pairs are sorted by encoded key=value (ordinal),
        // which for AWS canonicalisation equals "sort by key, then by value".
        var query = uri.Query.TrimStart('?');
        var sortedQuery = string.Join("&",
            (query.Length > 0 ? query.Split('&') : Array.Empty<string>())
            .Select(part =>
            {
                var eq = part.IndexOf('=');
                return eq < 0
                    ? Uri.EscapeDataString(Uri.UnescapeDataString(part)) + "="
                    : Uri.EscapeDataString(Uri.UnescapeDataString(part[..eq]))
                      + "="
                      + Uri.EscapeDataString(Uri.UnescapeDataString(part[(eq + 1)..]));
            })
            .OrderBy(x => x, StringComparer.Ordinal));

        var canonicalUri = string.IsNullOrEmpty(uri.AbsolutePath) ? "/" : uri.AbsolutePath;

        var canonicalRequest =
            method + "\n" +
            canonicalUri + "\n" +
            sortedQuery + "\n" +
            canonicalHeadersStr + "\n" +
            signedHeadersStr + "\n" +
            payloadHash;

        // Delegate signing entirely to AWS4Signer — no hand-rolled HMAC here.
        // ComputeSignature is a static method that internally derives the signing key
        // via four rounds of HMAC-SHA256 and computes the final signature.
        var signingResult = AWS4Signer.ComputeSignature(
            immutable,
            auth.Region,
            now,
            auth.Service,
            signedHeadersStr,
            canonicalRequest);

        // Apply signed headers to the request
        request.Headers.TryAddWithoutValidation("x-amz-date", amzDate);
        request.Headers.TryAddWithoutValidation("x-amz-content-sha256", payloadHash);
        if (!string.IsNullOrEmpty(immutable.Token))
            request.Headers.TryAddWithoutValidation("x-amz-security-token", immutable.Token);
        // ForAuthorizationHeader = "AWS4-HMAC-SHA256 Credential=…, SignedHeaders=…, Signature=…"
        request.Headers.TryAddWithoutValidation("Authorization", signingResult.ForAuthorizationHeader);
    }
}
