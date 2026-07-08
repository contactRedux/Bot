# AWS Cloud Deployment Guide

> **Related env vars:** `AWS_REGION`, `AWS_ACCOUNT_ID`, `S3_BUCKET_NAME` in `.env.example`

This document explains the target AWS architecture for the platform and how to migrate from local development to cloud deployment.

---

## Table of Contents

1. [Why AWS?](#1-why-aws)
2. [Target Architecture](#2-target-architecture)
3. [S3 — Model Artifacts and Backtest Reports](#3-s3--model-artifacts-and-backtest-reports)
4. [RDS — Production Database](#4-rds--production-database)
5. [ECS Fargate — Containerised API Server](#5-ecs-fargate--containerised-api-server)
6. [Secrets Manager — API Key Storage](#6-secrets-manager--api-key-storage)
7. [CloudWatch — Logging and Alerting](#7-cloudwatch--logging-and-alerting)
8. [Migration Path: Local → AWS](#8-migration-path-local--aws)
9. [Cost Estimate](#9-cost-estimate)

---

## 1. Why AWS?

AWS was chosen over GCP and Azure for this project based on:

- **Ecosystem breadth:** S3, RDS, ECS, Lambda, CloudWatch, Secrets Manager are all mature, well-documented services that cover every layer of the stack
- **SageMaker:** AWS's managed ML training/deployment service is available as a natural upgrade path for model training at scale
- **Cost:** The free tier covers development; spot instances and Fargate's pay-per-second billing keep production costs low for personal projects
- **Familiarity:** AWS has the largest share of finance industry infrastructure — learning it here is directly applicable to quant desk environments

---

## 2. Target Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  AWS Account (us-east-1)                                     │
│                                                              │
│  ┌─────────────┐    ┌──────────────────┐    ┌────────────┐  │
│  │  ECS Fargate│    │  Amazon RDS      │    │  Amazon S3 │  │
│  │  quant-engine│──▶│  PostgreSQL 15   │    │  models/   │  │
│  │  :8000      │    │  (Multi-AZ)      │    │  reports/  │  │
│  └──────┬──────┘    └──────────────────┘    └────────────┘  │
│         │                                         ▲          │
│         │ push model artifacts                    │          │
│         └─────────────────────────────────────────┘          │
│                                                              │
│  ┌──────────────────┐    ┌──────────────────────────────┐   │
│  │  AWS Secrets     │    │  Amazon CloudWatch           │   │
│  │  Manager         │    │  - Logs (structlog JSON)     │   │
│  │  (API keys, DB   │    │  - Metrics (drawdown, VaR)   │   │
│  │   passwords)     │    │  - Alarms (risk breach →SNS) │   │
│  └──────────────────┘    └──────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘

        ↑  HTTPS
   React Dashboard
   (served from S3 static site / CloudFront)
```

---

## 3. S3 — Model Artifacts and Backtest Reports

The `models/registry.py` versioned model registry stores artifacts locally during development. In production, replace the local JSON index + file paths with S3 paths.

**Bucket structure:**
```
s3://${S3_BUCKET_NAME}/
├── models/
│   ├── lstm_forecaster/
│   │   ├── v1_2024-01-15/   model.pt, metadata.json
│   │   └── v2_2024-02-01/   model.pt, metadata.json
│   ├── transformer_signal/
│   └── ensemble/
└── reports/
    ├── backtest_2024-01-15_run-abc123.json
    └── ...
```

**Python SDK usage (boto3):**

```python
import boto3
s3 = boto3.client("s3", region_name=os.environ["AWS_REGION"])

# Upload model
s3.upload_file("local_model.pt", S3_BUCKET_NAME, "models/lstm/v1/model.pt")

# Download model
s3.download_file(S3_BUCKET_NAME, "models/lstm/v1/model.pt", "local_model.pt")
```

**IAM policy required (least-privilege):**
```json
{
  "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
  "Resource": ["arn:aws:s3:::your-bucket", "arn:aws:s3:::your-bucket/*"]
}
```

---

## 4. RDS — Production Database

Replace the SQLite `DATABASE_URL` with an RDS PostgreSQL endpoint.

**Why PostgreSQL over SQLite in production:**
- Concurrent writes (multiple workers / the API + pipeline simultaneously)
- Full-text search for news articles
- Time-series extensions (TimescaleDB) available
- Automatic backups and point-in-time recovery

**Minimal setup:**
```bash
# Create RDS instance (via AWS Console or CLI)
aws rds create-db-instance \
  --db-instance-identifier algodb \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --master-username algoadmin \
  --master-user-password $DB_PASSWORD \
  --allocated-storage 20
```

**Update `.env`:**
```
DATABASE_URL=postgresql+asyncpg://algoadmin:${DB_PASSWORD}@<rds-endpoint>:5432/algodb
```

No code changes required — `data/store.py` uses SQLAlchemy which handles both SQLite and PostgreSQL transparently.

---

## 5. ECS Fargate — Containerised API Server

ECS Fargate runs the quant-engine API server as a container without managing EC2 instances.

**Dockerfile** (compliant with IBM security policy — Red Hat UBI, non-root user):
```dockerfile
FROM registry.redhat.io/ubi9/python-311-minimal:latest

RUN useradd -m -u 1001 appuser
WORKDIR /app

COPY packages/quant-engine/pyproject.toml .
RUN pip install --no-cache-dir -e ".[data,ml,api]"

COPY packages/quant-engine/ .
USER 1001

CMD ["uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8000"]
```

**Task definition key settings:**
- CPU: 1024 (1 vCPU), Memory: 2048 MB (minimum for PyTorch)
- Environment variables injected from Secrets Manager (not hardcoded)
- CloudWatch log driver: `awslogs`

---

## 6. Secrets Manager — API Key Storage

Never store API keys in environment variables on ECS task definitions (they appear in plaintext in the AWS console). Use Secrets Manager instead.

```python
# config/settings.py — retrieve secrets at startup
import boto3, json

def get_secret(name: str) -> dict:
    client = boto3.client("secretsmanager", region_name=os.environ["AWS_REGION"])
    resp   = client.get_secret_value(SecretId=name)
    return json.loads(resp["SecretString"])

secrets = get_secret("algo-trading/api-keys")
ALPACA_API_KEY = secrets["alpaca_api_key"]
```

**Store the following secrets in Secrets Manager:**
- `algo-trading/api-keys` — Alpaca, Binance, NewsAPI, Alpha Vantage, Bloomberg
- `algo-trading/db` — RDS username + password

---

## 7. CloudWatch — Logging and Alerting

The platform uses `structlog` with JSON output (`LOG_JSON=true` in production). CloudWatch ingests JSON logs and makes them searchable.

**Key alarms to configure:**
| Alarm | Metric | Threshold | Action |
|-------|--------|-----------|--------|
| Trading halted | Custom metric `trading_halted` | = 1 | SNS email/SMS |
| High drawdown | `current_drawdown_pct` | > 15% | SNS alert |
| API error rate | ECS 5xx responses | > 1% | SNS alert |
| Model inference failure | Log filter: "model error" | > 0 | SNS alert |

---

## 8. Migration Path: Local → AWS

| Step | Local (dev) | AWS (prod) |
|------|-------------|-----------|
| Database | `sqlite:///./algo_trading.db` | RDS PostgreSQL endpoint |
| Model storage | `packages/quant-engine/model_registry/` | `s3://${S3_BUCKET_NAME}/models/` |
| API server | `uvicorn api.main:app --reload` | ECS Fargate task |
| Secrets | `.env` file | AWS Secrets Manager |
| Logs | stdout / `logs/` | CloudWatch Logs |
| Dashboard | `npm run dev` (Vite HMR) | S3 static site + CloudFront |

**The application code requires zero changes** between local and AWS. All configuration is controlled by environment variables.

---

## 9. Cost Estimate

For a personal / paper-trading deployment (single region, us-east-1):

| Service | Configuration | Monthly Cost |
|---------|--------------|-------------|
| ECS Fargate | 0.25 vCPU, 0.5 GB, 730 hrs | ~$8 |
| RDS | db.t3.micro, 20 GB, single-AZ | ~$15 |
| S3 | 10 GB storage, low request count | ~$0.25 |
| CloudWatch | 5 GB logs | ~$2.50 |
| Secrets Manager | 2 secrets | ~$0.80 |
| **Total** | | **~$26/month** |

Free tier covers the first 12 months of RDS (750 hrs/month db.t3.micro) and reduces the cost to ~$11/month initially.
