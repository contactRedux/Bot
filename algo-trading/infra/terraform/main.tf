# infra/terraform/main.tf
#
# Root module — wires together the sub-modules and defines common data sources.
# All actual resource definitions live in the focused files (network.tf, ecs.tf, etc.).

# ── Data sources ─────────────────────────────────────────────────────────────

# Resolve the list of available AZs in the chosen region dynamically so the
# config is portable across regions.
data "aws_availability_zones" "available" {
  state = "available"
}

# Current caller identity — used to validate aws_account_id and build ARNs.
data "aws_caller_identity" "current" {}

# ── Common tags applied to every resource ────────────────────────────────────
#
# Using local.common_tags via resource-level tags blocks keeps naming
# consistent and searchable in Cost Explorer / Resource Groups.

locals {
  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  # Shorten frequently used prefix
  name_prefix = "${var.project}-${var.environment}"

  # Resolved AZs (up to az_count)
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)
}
