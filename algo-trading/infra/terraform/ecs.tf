# infra/terraform/ecs.tf
#
# ECS Fargate cluster, task definition, and service for the quant-engine API.
#
# Security:
#   - Task runs as non-root UID 1001 (set in the Dockerfile).
#   - readonlyRootFilesystem = true (tmpfs mount for /tmp).
#   - All secrets injected from Secrets Manager — never plaintext in task def.
#   - No public IP assigned to the task; egress via NAT only.
#   - Security group allows inbound 8000 only from within the VPC (ALB sg).

# ── Security group ────────────────────────────────────────────────────────────

resource "aws_security_group" "ecs_tasks" {
  name        = "${local.name_prefix}-ecs-tasks-sg"
  description = "Allow inbound on app port from within VPC; all egress."
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "App port from VPC CIDR (ALB or internal callers only)"
    from_port   = var.app_port
    to_port     = var.app_port
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "Allow all outbound (NAT will restrict external)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-ecs-tasks-sg" })
}

# ── ECS cluster ───────────────────────────────────────────────────────────────

resource "aws_ecs_cluster" "main" {
  name = "${local.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-cluster" })
}

# ── CloudWatch log group ──────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${local.name_prefix}"
  retention_in_days = 90 # minimum per security policy

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-ecs-logs" })
}

# ── Task definition ───────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "quant_engine" {
  family                   = "${local.name_prefix}-quant-engine"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name  = "quant-engine"
      image = "${aws_ecr_repository.quant_engine.repository_url}:${var.container_image_tag}"

      portMappings = [
        { containerPort = var.app_port, protocol = "tcp" }
      ]

      # Non-root execution and read-only root filesystem
      user              = "1001"
      readonlyRootFilesystem = true

      # /tmp writable via tmpfs mount (needed for SQLite dev mode and model files)
      mountPoints = []
      volumesFrom = []

      linuxParameters = {
        tmpfs = [
          { containerPath = "/tmp", size = 512 }
        ]
        # Drop all Linux capabilities; add none
        capabilities = {
          drop = ["ALL"]
          add  = []
        }
      }

      # Secrets injected from Secrets Manager (never plaintext)
      secrets = [
        {
          name      = "ALPACA_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.api_keys.arn}:alpaca_api_key::"
        },
        {
          name      = "ALPACA_SECRET_KEY"
          valueFrom = "${aws_secretsmanager_secret.api_keys.arn}:alpaca_secret_key::"
        },
        {
          name      = "BINANCE_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.api_keys.arn}:binance_api_key::"
        },
        {
          name      = "BINANCE_SECRET_KEY"
          valueFrom = "${aws_secretsmanager_secret.api_keys.arn}:binance_secret_key::"
        },
        {
          name      = "NEWSAPI_KEY"
          valueFrom = "${aws_secretsmanager_secret.api_keys.arn}:newsapi_key::"
        },
        {
          name      = "DATABASE_URL"
          valueFrom = "${aws_secretsmanager_secret.db_credentials.arn}:database_url::"
        }
      ]

      # Non-sensitive runtime environment variables
      environment = [
        { name = "TRADING_MODE",    value = "paper" },
        { name = "LOG_JSON",        value = "true" },
        { name = "LOG_LEVEL",       value = "INFO" },
        { name = "AWS_REGION",      value = var.aws_region },
        { name = "S3_BUCKET_NAME",  value = var.s3_bucket_name },
        { name = "BINANCE_TESTNET", value = "true" }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "quant-engine"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://127.0.0.1:${var.app_port}/api/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  ])

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-task-def" })
}

# ── ECS service ───────────────────────────────────────────────────────────────

resource "aws_ecs_service" "quant_engine" {
  name            = "${local.name_prefix}-quant-engine"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.quant_engine.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false # private subnet + NAT only
  }

  # Enable ECS Exec for debugging (operator-only via IAM)
  enable_execute_command = true

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-service" })

  depends_on = [aws_iam_role_policy_attachment.ecs_execution]
}
