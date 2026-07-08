# infra/terraform/s3.tf
#
# S3 bucket for model artifacts and backtest reports.
#
# Security:
#   - Public access blocked on all four dimensions.
#   - Server-side encryption with AES-256 (SSE-S3).
#   - Versioning enabled — protects against accidental deletion of model files.
#   - Lifecycle policy transitions old backtest reports to Glacier after 90 days.

resource "aws_s3_bucket" "artifacts" {
  bucket = var.s3_bucket_name

  tags = merge(local.common_tags, { Name = var.s3_bucket_name })
}

# Block all public access
resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Server-side encryption (AES-256)
resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# Versioning — protect model files from accidental overwrite/deletion
resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Lifecycle policy — move old backtest reports to Glacier after 90 days
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "archive-old-backtest-reports"
    status = "Enabled"

    filter {
      prefix = "reports/"
    }

    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    expiration {
      days = 365
    }
  }

  rule {
    id     = "expire-old-model-versions"
    status = "Enabled"

    filter {
      prefix = "models/"
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}
