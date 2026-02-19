# Caching Analysis for AWS AMI Resolver

## Original Caching Features Found

### CachingAMIResolver Features:
- Runtime caching with RuntimeAMICache
- Graceful fallback when AWS unavailable
- Failed parameter tracking to avoid retry storms
- Configurable behavior via AMIResolutionConfig
- Cache statistics and management

### RuntimeAMICache Features:
- In-memory cache (SSM parameter -> AMI ID)
- Failed parameter tracking
- Cache statistics
- Simple get/set/clear operations
- No TTL (runtime-only cache)

## Current AWS Resolver Analysis

### Methods:
- resolve_image_id(image_reference: str) -> str
- supports_reference_format(image_reference: str) -> bool
- _resolve_ssm_parameter(ssm_path: str) -> str
- _is_custom_alias(reference: str) -> bool
- _resolve_custom_alias(alias: str) -> str

### Current Dependencies:
- domain.template.image_resolver.ImageResolver (base class)
- boto3 for SSM operations
- botocore.exceptions for error handling

## Integration Plan

1. Add caching layer to current resolver
2. Enhance with TTL-based expiration
3. Add persistent cache support
4. Add configuration-driven behavior
5. Add sophisticated fallback mechanisms
6. Keep all code in AWS provider layer