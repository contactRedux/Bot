# infra/terraform/variables.tf
#
# All input variables for the algo-trading infrastructure.
# Sensitive values (passwords, API keys) are marked sensitive = true so they
# are redacted from plan/apply output and never stored in state as plaintext.

# ── Core ─────────────────────────────────────────────────────────────────────

variable "aws_region" {
  type        = string
  description = "AWS region for all resources."
  default     = "us-east-1"
}

variable "aws_account_id" {
  type        = string
  description = "AWS account ID. Used to construct ARNs."
}

variable "environment" {
  type        = string
  description = "Deployment environment tag (e.g. dev, staging, prod)."
  default     = "prod"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "project" {
  type        = string
  description = "Project name used as a prefix on all resource names and tags."
  default     = "algo-trading"
}

# ── Network ───────────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  type        = string
  description = "CIDR block for the VPC."
  default     = "10.0.0.0/16"
}

variable "az_count" {
  type        = number
  description = "Number of availability zones to spread subnets across (2 or 3)."
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3."
  }
}

# ── ECR / ECS ─────────────────────────────────────────────────────────────────

variable "container_image_tag" {
  type        = string
  description = "Docker image tag to deploy to ECS (e.g. sha-abc1234 or latest)."
  default     = "latest"
}

variable "task_cpu" {
  type        = number
  description = "ECS task CPU units (256 = 0.25 vCPU, 1024 = 1 vCPU)."
  default     = 1024
}

variable "task_memory" {
  type        = number
  description = "ECS task memory in MiB (minimum 512 for the full ML stack)."
  default     = 2048
}

variable "app_port" {
  type        = number
  description = "Container port that the FastAPI server listens on."
  default     = 8000
}

# ── RDS ───────────────────────────────────────────────────────────────────────

variable "db_instance_class" {
  type        = string
  description = "RDS instance class."
  default     = "db.t3.micro"
}

variable "db_allocated_storage" {
  type        = number
  description = "Initial RDS allocated storage in GiB."
  default     = 20
}

variable "db_name" {
  type        = string
  description = "PostgreSQL database name."
  default     = "algodb"
}

variable "db_username" {
  type        = string
  description = "RDS master username."
  default     = "algoadmin"
}

variable "db_password" {
  type        = string
  description = "RDS master password. Stored only in Secrets Manager, not in state."
  sensitive   = true
}

# ── S3 ────────────────────────────────────────────────────────────────────────

variable "s3_bucket_name" {
  type        = string
  description = "Name of the S3 bucket for model artifacts and backtest reports."
}
