"""Storage layer constants."""

# Default provider type for records written before provider_type was persisted.
# Do not remove: legacy data may not have this field. Remove only after a data
# migration backfills provider_type on all existing records.
LEGACY_DEFAULT_PROVIDER_TYPE = "aws"
