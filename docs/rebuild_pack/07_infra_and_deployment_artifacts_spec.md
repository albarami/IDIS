# Infrastructure and Deployment Artifacts Specification

**Version:** 1.0.0  
**Date:** 2026-02-05  
**Status:** Build Spec  
**Reference:** IDIS_Technical_Infrastructure_v6_3.md

---

## 1. Overview

This document specifies the deployment artifacts required for IDIS local development and staging environments. Production IaC (Terraform) is deferred but contracts are defined here.

---

## 2. Local Development Stack

### 2.1 docker-compose.yml

```yaml
version: "3.9"

services:
  # PostgreSQL 16 with extensions
  postgres:
    image: postgres:16-alpine
    container_name: idis-postgres
    environment:
      POSTGRES_USER: idis
      POSTGRES_PASSWORD: idis_dev_password
      POSTGRES_DB: idis
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/init-db.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U idis"]
      interval: 5s
      timeout: 5s
      retries: 5

  # Redis for caching and rate limiting
  redis:
    image: redis:7-alpine
    container_name: idis-redis
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  # MinIO for S3-compatible object storage
  minio:
    image: minio/minio:latest
    container_name: idis-minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: idis_access_key
      MINIO_ROOT_PASSWORD: idis_secret_key
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - minio_data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 5s
      timeout: 5s
      retries: 5

  # OpenTelemetry Collector
  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    container_name: idis-otel
    command: ["--config=/etc/otel-collector-config.yaml"]
    volumes:
      - ./config/otel-collector-config.yaml:/etc/otel-collector-config.yaml
    ports:
      - "4317:4317"   # OTLP gRPC
      - "4318:4318"   # OTLP HTTP
      - "8888:8888"   # Prometheus metrics

  # Jaeger for trace visualization
  jaeger:
    image: jaegertracing/all-in-one:latest
    container_name: idis-jaeger
    ports:
      - "16686:16686"  # UI
      - "14268:14268"  # HTTP collector
    environment:
      COLLECTOR_OTLP_ENABLED: "true"

  # IDIS Backend API
  api:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: idis-api
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio:
        condition: service_healthy
    environment:
      IDIS_DATABASE_URL: postgresql://idis:idis_dev_password@postgres:5432/idis
      IDIS_REDIS_URL: redis://redis:6379/0
      IDIS_OBJECT_STORE_TYPE: s3
      IDIS_S3_ENDPOINT: http://minio:9000
      IDIS_S3_ACCESS_KEY: idis_access_key
      IDIS_S3_SECRET_KEY: idis_secret_key
      IDIS_S3_BUCKET: idis-documents
      IDIS_OTEL_ENDPOINT: http://otel-collector:4317
      IDIS_ENV: development
    ports:
      - "8000:8000"
    volumes:
      - ./src:/app/src:ro
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 10s
      timeout: 5s
      retries: 3

  # IDIS Frontend
  ui:
    build:
      context: ./ui
      dockerfile: Dockerfile
    container_name: idis-ui
    depends_on:
      - api
    environment:
      NEXT_PUBLIC_API_URL: http://localhost:8000
    ports:
      - "3000:3000"

volumes:
  postgres_data:
  redis_data:
  minio_data:
```

### 2.2 Dockerfile (Backend)

```dockerfile
FROM python:3.11-slim as base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# Copy source code
COPY src/ ./src/
COPY openapi/ ./openapi/
COPY schemas/ ./schemas/

# Create non-root user
RUN useradd -m -u 1000 idis
USER idis

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run application
EXPOSE 8000
CMD ["uvicorn", "idis.api.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--factory"]
```

### 2.3 Dockerfile (Frontend)

```dockerfile
FROM node:20-alpine as base

WORKDIR /app

# Install dependencies
COPY package.json package-lock.json ./
RUN npm ci

# Copy source
COPY . .

# Build
RUN npm run build

# Production image
FROM node:20-alpine as production
WORKDIR /app

COPY --from=base /app/.next/standalone ./
COPY --from=base /app/.next/static ./.next/static
COPY --from=base /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
```

---

## 3. Kubernetes Manifests (Staging)

### 3.1 Namespace

```yaml
# k8s/base/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: idis-staging
  labels:
    app.kubernetes.io/name: idis
    environment: staging
```

### 3.2 ConfigMap

```yaml
# k8s/base/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: idis-config
  namespace: idis-staging
data:
  IDIS_ENV: "staging"
  IDIS_LOG_LEVEL: "INFO"
  IDIS_OTEL_ENDPOINT: "http://otel-collector.observability:4317"
  IDIS_RATE_LIMIT_USER: "600"
  IDIS_RATE_LIMIT_INTEGRATION: "1200"
```

### 3.3 Secrets (Template)

```yaml
# k8s/base/secrets.yaml
apiVersion: v1
kind: Secret
metadata:
  name: idis-secrets
  namespace: idis-staging
type: Opaque
stringData:
  IDIS_DATABASE_URL: "postgresql://idis:PASSWORD@postgres.idis-staging:5432/idis"
  IDIS_REDIS_URL: "redis://redis.idis-staging:6379/0"
  IDIS_S3_ACCESS_KEY: "REPLACE_ME"
  IDIS_S3_SECRET_KEY: "REPLACE_ME"
  IDIS_JWT_SECRET: "REPLACE_ME"
  IDIS_ENCRYPTION_KEY: "REPLACE_ME"
```

### 3.4 Deployment

```yaml
# k8s/base/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: idis-api
  namespace: idis-staging
  labels:
    app: idis-api
spec:
  replicas: 2
  selector:
    matchLabels:
      app: idis-api
  template:
    metadata:
      labels:
        app: idis-api
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8000"
    spec:
      serviceAccountName: idis-api
      containers:
        - name: api
          image: ghcr.io/albarami/idis:staging
          imagePullPolicy: Always
          ports:
            - containerPort: 8000
              name: http
          envFrom:
            - configMapRef:
                name: idis-config
            - secretRef:
                name: idis-secrets
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              cpu: "2000m"
              memory: "2Gi"
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 5
          securityContext:
            runAsNonRoot: true
            runAsUser: 1000
            readOnlyRootFilesystem: true
            allowPrivilegeEscalation: false
```

### 3.5 Service

```yaml
# k8s/base/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: idis-api
  namespace: idis-staging
spec:
  selector:
    app: idis-api
  ports:
    - port: 80
      targetPort: 8000
      name: http
  type: ClusterIP
```

### 3.6 Ingress

```yaml
# k8s/base/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: idis-api
  namespace: idis-staging
  annotations:
    kubernetes.io/ingress.class: nginx
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
    - hosts:
        - api-staging.idis.example.com
      secretName: idis-api-tls
  rules:
    - host: api-staging.idis.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: idis-api
                port:
                  number: 80
```

---

## 4. Environment Variable Catalog

### 4.1 Required Variables

| Variable | Description | Secret | Default |
|----------|-------------|--------|---------|
| `IDIS_DATABASE_URL` | PostgreSQL connection string | ✅ | — |
| `IDIS_REDIS_URL` | Redis connection string | ✅ | — |
| `IDIS_JWT_SECRET` | JWT signing secret | ✅ | — |
| `IDIS_ENCRYPTION_KEY` | AES-256 key for credential encryption | ✅ | — |

### 4.2 Optional Variables

| Variable | Description | Secret | Default |
|----------|-------------|--------|---------|
| `IDIS_ENV` | Environment name | ❌ | `development` |
| `IDIS_LOG_LEVEL` | Logging level | ❌ | `INFO` |
| `IDIS_PORT` | API port | ❌ | `8000` |
| `IDIS_WORKERS` | Uvicorn worker count | ❌ | `1` |

### 4.3 Object Storage Variables

| Variable | Description | Secret | Default |
|----------|-------------|--------|---------|
| `IDIS_OBJECT_STORE_TYPE` | Storage type: `s3`, `azure`, `gcs`, `filesystem` | ❌ | `filesystem` |
| `IDIS_S3_ENDPOINT` | S3-compatible endpoint URL | ❌ | AWS default |
| `IDIS_S3_BUCKET` | S3 bucket name | ❌ | `idis-documents` |
| `IDIS_S3_ACCESS_KEY` | S3 access key | ✅ | — |
| `IDIS_S3_SECRET_KEY` | S3 secret key | ✅ | — |
| `IDIS_S3_REGION` | S3 region | ❌ | `us-east-1` |

### 4.4 Observability Variables

| Variable | Description | Secret | Default |
|----------|-------------|--------|---------|
| `IDIS_OTEL_ENDPOINT` | OpenTelemetry collector endpoint | ❌ | — |
| `IDIS_OTEL_SERVICE_NAME` | Service name for traces | ❌ | `idis-api` |
| `IDIS_METRICS_ENABLED` | Enable Prometheus metrics | ❌ | `true` |

### 4.5 LLM Provider Variables

| Variable | Description | Secret | Default |
|----------|-------------|--------|---------|
| `IDIS_LLM_PROVIDER` | LLM provider: `openai`, `anthropic`, `azure` | ❌ | `openai` |
| `IDIS_OPENAI_API_KEY` | OpenAI API key | ✅ | — |
| `IDIS_ANTHROPIC_API_KEY` | Anthropic API key | ✅ | — |
| `IDIS_AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint | ❌ | — |
| `IDIS_AZURE_OPENAI_KEY` | Azure OpenAI key | ✅ | — |

### 4.6 Rate Limiting Variables

| Variable | Description | Secret | Default |
|----------|-------------|--------|---------|
| `IDIS_RATE_LIMIT_USER` | Requests/min for users | ❌ | `600` |
| `IDIS_RATE_LIMIT_INTEGRATION` | Requests/min for integrations | ❌ | `1200` |

---

## 5. Configuration Files

### 5.1 OTel Collector Config

```yaml
# config/otel-collector-config.yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 5s
    send_batch_size: 1000

exporters:
  jaeger:
    endpoint: jaeger:14250
    tls:
      insecure: true
  
  prometheus:
    endpoint: 0.0.0.0:8889

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [jaeger]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [prometheus]
```

### 5.2 Database Init Script

```sql
-- scripts/init-db.sql

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Create audit schema
CREATE SCHEMA IF NOT EXISTS audit;

-- Set default search path
ALTER DATABASE idis SET search_path TO public, audit;

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE idis TO idis;
GRANT ALL PRIVILEGES ON SCHEMA public TO idis;
GRANT ALL PRIVILEGES ON SCHEMA audit TO idis;
```

---

## 6. CI/CD Integration

### 6.1 GitHub Actions Workflow

```yaml
# .github/workflows/deploy-staging.yml
name: Deploy to Staging

on:
  push:
    branches: [main]

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      
      - name: Login to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ghcr.io/albarami/idis:staging,${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy:
    needs: build-and-push
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up kubectl
        uses: azure/setup-kubectl@v3
      
      - name: Configure kubeconfig
        run: |
          echo "${{ secrets.KUBE_CONFIG }}" > kubeconfig
          export KUBECONFIG=kubeconfig
      
      - name: Deploy to staging
        run: |
          kubectl set image deployment/idis-api \
            api=ghcr.io/albarami/idis:${{ github.sha }} \
            -n idis-staging
          kubectl rollout status deployment/idis-api -n idis-staging
```

---

## 7. Production Contract (Deferred)

### 7.1 Required Infrastructure

| Component | Service | Notes |
|-----------|---------|-------|
| Compute | AWS EKS / GKE / AKS | Managed Kubernetes |
| Database | AWS RDS / Cloud SQL | PostgreSQL 16, Multi-AZ |
| Cache | AWS ElastiCache / Memorystore | Redis 7 |
| Object Storage | AWS S3 / GCS / Azure Blob | WORM for audit |
| KMS | AWS KMS / Cloud KMS | Per-tenant keys |
| CDN | CloudFront / Cloud CDN | Static assets |
| WAF | AWS WAF / Cloud Armor | DDoS protection |
| Monitoring | Datadog / New Relic | APM, logs, metrics |

### 7.2 Terraform Module Contract

```hcl
# Contract for future Terraform implementation

module "idis_production" {
  source = "./modules/idis"
  
  # Required
  environment     = "production"
  region          = "us-east-1"
  vpc_cidr        = "10.0.0.0/16"
  
  # Database
  db_instance_class   = "db.r6g.xlarge"
  db_multi_az         = true
  db_backup_retention = 30
  
  # Compute
  eks_node_instance_type = "m6i.xlarge"
  eks_node_min_count     = 3
  eks_node_max_count     = 10
  
  # Storage
  s3_versioning_enabled = true
  s3_worm_enabled       = true
  
  # Networking
  enable_waf            = true
  enable_ddos_protection = true
}
```

---

## 8. Acceptance Criteria

### 8.1 Local Development
- [ ] `docker-compose up` starts all services
- [ ] API responds on localhost:8000
- [ ] UI responds on localhost:3000
- [ ] Postgres, Redis, MinIO accessible
- [ ] Traces visible in Jaeger

### 8.2 Staging Deployment
- [ ] K8s manifests apply without error
- [ ] API deployment rolls out successfully
- [ ] Health checks pass
- [ ] TLS termination works
- [ ] Secrets properly mounted

### 8.3 Environment Variables
- [ ] All required vars documented
- [ ] Secret vars identified
- [ ] Defaults documented
- [ ] Validation at startup
