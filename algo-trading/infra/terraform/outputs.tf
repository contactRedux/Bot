# infra/terraform/outputs.tf
#
# Exported values useful for CI/CD pipelines, manual verification, and
# connecting the application config to the deployed infrastructure.

output "ecr_repository_url" {
  description = "ECR repository URL for the quant-engine image."
  value       = aws_ecr_repository.quant_engine.repository_url
}

output "ecs_cluster_name" {
  description = "Name of the ECS Fargate cluster."
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "Name of the ECS service running the quant-engine."
  value       = aws_ecs_service.quant_engine.name
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint (host:port). Used to build DATABASE_URL."
  value       = aws_db_instance.main.endpoint
}

output "rds_db_name" {
  description = "PostgreSQL database name."
  value       = aws_db_instance.main.db_name
}

output "s3_bucket_name" {
  description = "Name of the S3 artifacts bucket."
  value       = aws_s3_bucket.artifacts.bucket
}

output "s3_bucket_arn" {
  description = "ARN of the S3 artifacts bucket."
  value       = aws_s3_bucket.artifacts.arn
}

output "api_keys_secret_arn" {
  description = "ARN of the Secrets Manager secret holding external API keys."
  value       = aws_secretsmanager_secret.api_keys.arn
  sensitive   = true
}

output "db_credentials_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the database URL."
  value       = aws_secretsmanager_secret.db_credentials.arn
  sensitive   = true
}

output "vpc_id" {
  description = "VPC ID."
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "List of private subnet IDs (ECS + RDS)."
  value       = aws_subnet.private[*].id
}
