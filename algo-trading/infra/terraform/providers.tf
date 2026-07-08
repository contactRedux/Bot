# infra/terraform/providers.tf
#
# Terraform provider configuration for the algo-trading platform.
# Only the AWS provider is required. The version constraint pins to the
# latest v5 release family to ensure a stable, deterministic baseline.

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
  }

  # Remote state backend — replace with your S3 bucket + DynamoDB table
  # before running in production. Commented out for local development.
  #
  # backend "s3" {
  #   bucket         = "my-algo-trading-tfstate"
  #   key            = "algo-trading/terraform.tfstate"
  #   region         = "us-east-1"
  #   encrypt        = true
  #   dynamodb_table = "algo-trading-tflock"
  # }
}

provider "aws" {
  region = var.aws_region
}
