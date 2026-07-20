package transport

import (
	"encoding/hex"
	"net/http"
	"strings"
	"testing"
	"time"
)

// TestDeriveSigningKey_AWSVector locks the signing-key derivation to the value
// published in the AWS SigV4 documentation (the "AWS4-HMAC-SHA256 signing key"
// example). This is the canonical known-good vector:
//
//	secret  = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
//	date    = "20150830"
//	region  = "us-east-1"
//	service = "iam"
//	=> c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9
func TestDeriveSigningKey_AWSVector(t *testing.T) {
	key := deriveSigningKey(
		"wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
		"20150830", "us-east-1", "iam",
	)
	got := hex.EncodeToString(key)
	const want = "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9"
	if got != want {
		t.Fatalf("signing key mismatch:\n got  %s\n want %s", got, want)
	}
}

// TestSha256Hex_EmptyBody locks the SHA-256 of the empty payload, the value AWS
// uses for requests without a body.
func TestSha256Hex_EmptyBody(t *testing.T) {
	const want = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
	if got := sha256Hex(nil); got != want {
		t.Fatalf("empty-body hash mismatch:\n got  %s\n want %s", got, want)
	}
}

// TestSignRequest_Deterministic verifies that signing is deterministic for a
// fixed request/time/creds and that the Authorization header has the expected
// structure and signed-header set.
func TestSignRequest_Deterministic(t *testing.T) {
	creds := AWSCredentials{
		AccessKeyID:     "AKIDEXAMPLE",
		SecretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
	}
	fixed := time.Date(2015, 8, 30, 12, 36, 0, 0, time.UTC)

	sign := func() string {
		req, _ := http.NewRequest(http.MethodGet, "https://example.amazonaws.com/api/v1/templates/", nil)
		if err := signRequest(req, nil, creds, "us-east-1", "execute-api", fixed); err != nil {
			t.Fatalf("signRequest: %v", err)
		}
		return req.Header.Get("Authorization")
	}

	a := sign()
	b := sign()
	if a != b {
		t.Fatalf("signing not deterministic:\n a=%s\n b=%s", a, b)
	}
	if !strings.HasPrefix(a, "AWS4-HMAC-SHA256 ") {
		t.Fatalf("unexpected auth scheme: %s", a)
	}
	if !strings.Contains(a, "Credential=AKIDEXAMPLE/20150830/us-east-1/execute-api/aws4_request") {
		t.Fatalf("credential scope missing/wrong: %s", a)
	}
	if !strings.Contains(a, "SignedHeaders=host;x-amz-date") {
		t.Fatalf("signed headers should be host;x-amz-date for a header-less GET: %s", a)
	}
	if !strings.Contains(a, "Signature=") {
		t.Fatalf("signature missing: %s", a)
	}
}

// TestSignRequest_SessionTokenSigned verifies temporary credentials add and
// sign the x-amz-security-token header.
func TestSignRequest_SessionTokenSigned(t *testing.T) {
	creds := AWSCredentials{
		AccessKeyID:     "AKIDEXAMPLE",
		SecretAccessKey: "secret",
		SessionToken:    "SESSIONTOKEN",
	}
	req, _ := http.NewRequest(http.MethodGet, "https://example.amazonaws.com/x", nil)
	if err := signRequest(req, nil, creds, "us-east-1", "execute-api", time.Unix(0, 0).UTC()); err != nil {
		t.Fatalf("signRequest: %v", err)
	}
	if req.Header.Get("x-amz-security-token") != "SESSIONTOKEN" {
		t.Fatal("expected x-amz-security-token header to be set")
	}
	auth := req.Header.Get("Authorization")
	if !strings.Contains(auth, "x-amz-security-token") {
		t.Fatalf("session token must be in SignedHeaders: %s", auth)
	}
}

// TestCanonicalQueryString_SortedAndEncoded verifies query params are sorted
// and RFC-3986 encoded in the canonical request.
func TestCanonicalQueryString_SortedAndEncoded(t *testing.T) {
	req, _ := http.NewRequest(http.MethodGet, "https://h/x?b=2&a=1&c=hello%20world", nil)
	got := buildCanonicalQueryString(req)
	const want = "a=1&b=2&c=hello%20world"
	if got != want {
		t.Fatalf("canonical query mismatch:\n got  %s\n want %s", got, want)
	}
}

// TestSigV4Transport_SignsAndForwards verifies the RoundTripper sets the
// Authorization + x-amz-date headers and forwards to Next.
func TestSigV4Transport_SignsAndForwards(t *testing.T) {
	var seen *http.Request
	tr := &SigV4Transport{
		Credentials: AWSCredentials{AccessKeyID: "AKID", SecretAccessKey: "secret"},
		Region:      "us-east-1",
		Next: rtFunc(func(req *http.Request) (*http.Response, error) {
			seen = req
			return newResp(200), nil
		}),
	}
	req, _ := http.NewRequest(http.MethodGet, "https://example.amazonaws.com/x", nil)
	if _, err := tr.RoundTrip(req); err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	if seen.Header.Get("Authorization") == "" {
		t.Fatal("expected Authorization header on forwarded request")
	}
	if seen.Header.Get("x-amz-date") == "" {
		t.Fatal("expected x-amz-date header on forwarded request")
	}
}
