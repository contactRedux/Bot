# infra/terraform/secrets.tf
#
# AWS Secrets Manager secrets for API keys and database credentials.
#
# Design:
#   - Secrets are created as empty shells by Terraform.
#   - Actual secret values are populated manually (aws secretsmanager put-secret-value)
#     or via CI/CD — NEVER stored in this file or in Terraform state as plaintext.
#   - The ECS execution role is granted read access in iam.tf.
#   - Automatic rotation is documented but not configured here (requires a Lambda
#     rotation function — out of scope for this baseline).

# ── API keys secret ───────────────────────────────────────────────────────────
# Stores all external service API keys as a JSON object.
# Expected structure:
# {
#   "alpaca_api_key":     "...",
#   "alpaca_secret_key":  "...",
#   "binance_api_key":    "...",
#   "binance_secret_key": "...",
#   "newsapi_key":        "...",
#   "alpha_vantage_key":  "...",
#   "bloomberg_app_name": "..."
# }

resource "aws_secretsmanager_secret" "api_keys" {
  name        = "${local.name_prefix}/api-keys"
  description = "External service API keys for the algo-trading platform."

  # Prevent accidental deletion in production
  recovery_window_in_days = 7

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-api-keys-secret" })
}

# Placeholder version — set real values via CLI before first deployment:
#   aws secretsmanager put-secret-value \
#     --secret-id algo-trading-prod/api-keys \
#     --secret-string '{"alpaca_api_key":"...","alpaca_secret_key":"...",...}'
resource "aws_secretsmanager_secret_version" "api_keys_placeholder" {
  secret_id = aws_secretsmanager_secret.api_keys.id
  secret_string = jsonencode({
    alpaca_api_key     = "REPLACE_ME"
    alpaca_secret_key  = "REPLACE_ME"
    binance_api_key    = "REPLACE_ME"
    binance_secret_key = "REPLACE_ME"
    newsapi_key        = "REPLACE_ME"
    alpha_vantage_key  = "REPLACE_ME"
    bloomberg_app_name = ""
  })

  lifecycle {
    # Prevent Terraform from overwriting values set outside Terraform
    ignore_changes = [secret_string]
  }
}

# ── Database credentials secret ───────────────────────────────────────────────

resource "aws_secretsmanager_secret" "db_credentials" {
  name        = "${local.name_prefix}/db"
  description = "RDS PostgreSQL connection string for the algo-trading platform."

  recovery_window_in_days = 7

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-db-secret" })
}

resource "aws_secretsmanager_secret_version" "db_credentials_placeholder" {
  secret_id = aws_secretsmanager_secret.db_credentials.id
  secret_string = jsonencode({
    database_url = "postgresql+asyncpg://${var.db_username}:REPLACE_ME@${aws_db_instance.main.endpoint}/${var.db_name}"
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}
