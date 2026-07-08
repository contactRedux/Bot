# infra/terraform/ecr.tf
#
# Amazon Elastic Container Registry — stores the quant-engine Docker image.
#
# Security:
#   - Image scanning on push (BASIC) catches known CVEs before deployment.
#   - Encryption with the default AWS-managed KMS key for ECR.
#   - Image tag immutability prevents overwriting a deployed image tag.
#   - Lifecycle policy keeps the last 10 tagged images, pruning untagged images
#     after 1 day to control storage costs.

resource "aws_ecr_repository" "quant_engine" {
  name                 = "${local.name_prefix}/quant-engine"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-ecr" })
}

resource "aws_ecr_lifecycle_policy" "quant_engine" {
  repository = aws_ecr_repository.quant_engine.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 10 tagged images"
        selection = {
          tagStatus   = "tagged"
          tagPrefixList = ["sha-", "v"]
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Expire untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      }
    ]
  })
}
