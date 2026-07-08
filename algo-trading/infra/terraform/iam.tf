# infra/terraform/iam.tf
#
# IAM roles and policies for ECS execution and task roles.
#
# Principle of least privilege:
#   - Execution role: only what Fargate needs to start the task (pull image,
#     write logs, read secrets).
#   - Task role: only what the application needs at runtime (S3 read/write,
#     CloudWatch custom metrics, ECS Exec).
#   - No wildcard resources on sensitive actions.

# ── ECS execution role ────────────────────────────────────────────────────────
# Used by the Fargate control plane to pull the container image and inject secrets.

data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${local.name_prefix}-ecs-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
  tags               = merge(local.common_tags, { Name = "${local.name_prefix}-ecs-execution-role" })
}

# Attach the AWS-managed policy for basic ECS task execution (ECR pull + CW logs)
resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Additional inline policy: read from Secrets Manager
data "aws_iam_policy_document" "ecs_secrets_read" {
  statement {
    sid    = "ReadApiKeys"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret"
    ]
    resources = [
      aws_secretsmanager_secret.api_keys.arn,
      aws_secretsmanager_secret.db_credentials.arn
    ]
  }
}

resource "aws_iam_role_policy" "ecs_secrets_read" {
  name   = "secrets-read"
  role   = aws_iam_role.ecs_execution.id
  policy = data.aws_iam_policy_document.ecs_secrets_read.json
}

# ── ECS task role ─────────────────────────────────────────────────────────────
# Used by the running application container at runtime.

resource "aws_iam_role" "ecs_task" {
  name               = "${local.name_prefix}-ecs-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
  tags               = merge(local.common_tags, { Name = "${local.name_prefix}-ecs-task-role" })
}

# S3: read/write to the artifacts bucket only
data "aws_iam_policy_document" "ecs_task_s3" {
  statement {
    sid    = "S3BucketList"
    effect = "Allow"
    actions = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.s3_bucket_name}"]
  }

  statement {
    sid    = "S3ObjectReadWrite"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject"
    ]
    resources = ["arn:aws:s3:::${var.s3_bucket_name}/*"]
  }
}

resource "aws_iam_role_policy" "ecs_task_s3" {
  name   = "s3-artifacts"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ecs_task_s3.json
}

# CloudWatch: put custom metrics for drawdown / VaR monitoring
data "aws_iam_policy_document" "ecs_task_cloudwatch" {
  statement {
    sid    = "PutCustomMetrics"
    effect = "Allow"
    actions = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["AlgoTrading/${var.environment}"]
    }
  }
}

resource "aws_iam_role_policy" "ecs_task_cloudwatch" {
  name   = "cloudwatch-metrics"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ecs_task_cloudwatch.json
}

# SSM messages — required for ECS Exec (operator debugging, tightly scoped)
data "aws_iam_policy_document" "ecs_exec" {
  statement {
    sid    = "ECSExec"
    effect = "Allow"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel"
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ecs_exec" {
  name   = "ecs-exec"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ecs_exec.json
}
