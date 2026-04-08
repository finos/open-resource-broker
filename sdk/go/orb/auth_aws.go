package orb

import (
	"net/http"

	"github.com/awslabs/open-resource-broker/sdk/go/internal/transport"
)

// AWSCredentials holds static AWS credentials for SigV4 signing.
// For temporary credentials (STS/IAM roles), set SessionToken.
type AWSCredentials = transport.AWSCredentials

// WithAWSSigV4 authenticates using AWS Signature Version 4 (stdlib implementation).
// credentials contains static AWS credentials.
// region is the AWS region (e.g. "us-east-1").
// service is the AWS service name (default "execute-api").
func WithAWSSigV4(credentials AWSCredentials, region, service string) AuthOption {
	return sigV4Auth{creds: credentials, region: region, service: service}
}

type sigV4Auth struct {
	creds   transport.AWSCredentials
	region  string
	service string
}

func (a sigV4Auth) wrap(next http.RoundTripper) http.RoundTripper {
	return &transport.SigV4Transport{
		Credentials: a.creds,
		Region:      a.region,
		Service:     a.service,
		Next:        next,
	}
}
