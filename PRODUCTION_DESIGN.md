# Birds API - Production Architecture (Module 2)

## 1. Executive summary

The Birds API is a stateless HTTP service that reads bird data from a database and enriches it with weather alerts from weather.gov. For production, I would run it on a managed Kubernetes platform (GKE, EKS, or AKS) behind an Ingress load balancer with TLS, deploy via GitOps (ArgoCD), replace SQLite with managed PostgreSQL, add caching and resilience around the external weather API, and instrument the stack with logs, metrics, traces, and SLO-based alerting.

The design prioritizes **operational simplicity**, **clear failure boundaries**, and **graceful degradation** - the app should return bird data even when weather.gov is slow or unavailable. This matches the behavior already implemented in Module 1, extended with production-grade infrastructure around it.

---

## 2. Target architecture

### High-level flow

```
User
  → DNS (Route 53 / Cloud DNS)
  → TLS termination (Ingress / cloud LB)
  → Ingress Controller (nginx / ALB / GCE)
  → Kubernetes Service (ClusterIP)
  → Birds API Pods (Deployment, N replicas)
       ├→ Managed PostgreSQL (RDS / Cloud SQL)
       └→ weather.gov API (external, cached)
```

### ASCII diagram

```
                        ┌─────────────────────────────────────────────┐
                        │              Internet / Users               │
                        └─────────────────────┬───────────────────────┘
                                              │ HTTPS
                                              ▼
                        ┌─────────────────────────────────────────────┐
                        │   DNS  (birds.api.example.com → LB IP)      │
                        └─────────────────────┬───────────────────────┘
                                              │
                                              ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         Kubernetes Cluster (multi-AZ)                        │
│                                                                              │
│   ┌─────────────────┐      ┌──────────────────┐      ┌────────────────────┐  │
│   │ Ingress + TLS   │─────▶│ Service :80      │─────▶│ Birds API Pods     │  │
│   │ (cert-manager)  │      │ (ClusterIP)      │      │ (gunicorn, HPA)    │  │
│   └─────────────────┘      └──────────────────┘      └─────────┬──────────┘  │
│                                                                   │            │
│                     ┌─────────────────────────────────────────────┤            │
│                     │                                             │            │
│                     ▼                                             ▼            │
│           ┌──────────────────┐                        ┌──────────────────┐     │
│           │ Managed Postgres │                        │ Redis cache      │     │
│           │ (RDS / Cloud SQL)│                        │ (weather alerts) │     │
│           └──────────────────┘                        └────────┬─────────┘     │
│                                                                │               │
└────────────────────────────────────────────────────────────────┼───────────────┘
                                                                 │ HTTPS
                                                                 ▼
                                                    ┌────────────────────────┐
                                                    │  api.weather.gov       │
                                                    │  (external dependency) │
                                                    └────────────────────────┘
```

### Design notes

- **Stateless pods** - all persistent state lives in PostgreSQL and Redis; pods are horizontally scalable.
- **Cache layer** - At small scale, an in-memory TTL cache may be enough. Redis becomes useful when multiple replicas need a shared cache.
- **Managed services** - offload database operations, backups, and patching to the cloud provider.

---

## 3. CI/CD

### Pipeline (GitHub Actions)

```
push/PR → lint + unit tests → build image → Trivy scan → push to registry → deploy
```

| Stage | Action |
|-------|--------|
| **Test** | Run `pytest` (app logic, validation, mocked weather API), `helm lint`, `helm template` dry-run |
| **Build** | `docker build` with versioned tag (`sha`, `semver`) |
| **Scan** | Trivy (or Snyk) for OS and dependency CVEs; fail on critical |
| **Push** | ECR / GCR / ACR - never deploy `birds-app:local` |
| **Deploy** | ArgoCD syncs Helm chart to target cluster/namespace |

### Environments

| Environment | Cluster / namespace | Trigger | Purpose |
|-------------|---------------------|---------|---------|
| **dev** | `birds-dev` | merge to `main` | integration testing |
| **staging** | `birds-staging` | release candidate tag | pre-prod validation |
| **prod** | `birds-prod` | approved tag + manual gate | live traffic |

Each environment gets its own values file (`values-dev.yaml`, `values-staging.yaml`, `values-prod.yaml`) overriding image tag, replica count, resource limits, and DB connection strings.

### Rollback strategy

- **Kubernetes:** `helm rollback birds <revision>` or ArgoCD "rollback to previous" - fast, preferred for bad deploys.
- **Image pin:** every deploy tags the exact image digest in Helm values; rollbacks are deterministic.
- **Database:** schema migrations run forward-only with reviewed rollback scripts for emergencies.

---

## 4. Kubernetes production setup

### Deployment

```yaml
# Illustrative - not applied in Module 1
replicas: 3   # minimum for HA; HPA scales beyond this

resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 500m
    memory: 256Mi
```

| Concern | Approach |
|---------|----------|
| **Replicas** | Start with 3; scale with HPA |
| **Probes** | `readinessProbe` and `livenessProbe` on `/health` (already in Module 1) |
| **HPA** | Start with CPU/memory targets (e.g. 70% CPU); min 3, max 20. Add request rate or other custom metrics through Prometheus Adapter/KEDA later if needed |
| **PDB** | `minAvailable: 2` - ensures at least two pods during node drains/upgrades |
| **Anti-affinity** | `podAntiAffinity` preferred across AZs - spreads replicas across failure domains |
| **Namespaces** | `birds-dev`, `birds-staging`, `birds-prod` - isolate RBAC, secrets, and quotas |

**Probe design:** Readiness should indicate whether the pod can serve traffic. Liveness should only detect a stuck process and should not depend on external services like weather.gov.

### Application server change

Replace `flask run` with **gunicorn**:

```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "app:app"]
```

Workers are tuned per CPU limit; for I/O-bound workloads (weather API calls), threads help.

---

## 5. Networking

| Layer | Choice | Rationale |
|-------|--------|-----------|
| **Ingress** | nginx Ingress or cloud LB (ALB / GCE Ingress) | TLS termination, path routing, rate limiting |
| **TLS** | cert-manager + Let's Encrypt or ACM-managed cert | Automated renewal |
| **DNS** | Route 53 / Cloud DNS → Ingress external IP or LB hostname | `birds.api.example.com` |
| **Rate limiting** | Ingress annotation or API gateway (optional) | Protect against abuse; weather.gov has its own limits |
| **WAF** | Cloud WAF (optional at scale) | Block common attacks before they reach pods |

### Internal vs external

- **Birds API** - external-facing via Ingress.
- **PostgreSQL** - private subnet only; no public endpoint.
- **Redis** - cluster-internal Service; not exposed externally.
- **weather.gov** - egress via NAT gateway; consider allowlisting and monitoring outbound traffic.

---

## 6. Data layer

### Why not SQLite in production

SQLite is embedded in the container filesystem. It does not support concurrent writes across replicas, has no built-in HA, and data is lost if a pod is rescheduled without a persistent volume. For a multi-replica Deployment, each pod would have its own copy - inconsistent and fragile.

### Managed PostgreSQL

| Concern | Approach |
|---------|----------|
| **Service** | AWS RDS PostgreSQL or GCP Cloud SQL |
| **Schema** | Alembic or Flyway migrations in CI/CD pipeline |
| **Backups** | Automated daily snapshots; point-in-time recovery (PITR) enabled |
| **Encryption** | At-rest (KMS) and in-transit (TLS to DB) |
| **Pooling** | PgBouncer sidecar or RDS Proxy - avoid connection exhaustion under load |
| **Read replicas** | Add when read traffic grows; bird data is read-heavy, weather is external |

### Migration from SQLite

One-time ETL script loads `birds.db` into PostgreSQL. Application code switches to `psycopg2` / SQLAlchemy with parameterized queries.

---

## 7. External dependency handling (weather.gov)

Module 1 already returns bird data when weather fails. Production extends this:

| Pattern | Implementation |
|---------|----------------|
| **Timeout** | 5–10s (already in app); tune per SLO |
| **Retry** | 2 retries with exponential backoff + jitter on 5xx/timeout |
| **Circuit breaker** | Open circuit after N consecutive failures; skip weather calls for 60s |
| **Cache** | In-memory TTL cache at small scale; shared Redis (`weather:{state}`, TTL 5–15 min) when running multiple replicas |
| **Partial response** | Return `{ "bird": [...], "weather": null, "weather_status": "unavailable" }` |
| **User-Agent** | Set a clear User-Agent with application/contact information as recommended by weather.gov |

This keeps the API available and fast even when weather.gov is degraded.

---

## 8. Observability

### Logging

- Structured JSON logs (Python `structlog` or `python-json-logger`)
- Include `request_id`, `state`, `latency_ms`, `weather_cache_hit`
- Ship to centralized store (CloudWatch, Datadog, Elastic)

### Metrics (Prometheus)

| Metric | Type | Purpose |
|--------|------|---------|
| `http_requests_total` | counter | Request volume by status/path |
| `http_request_duration_seconds` | histogram | Latency percentiles |
| `weather_api_errors_total` | counter | External dependency health |
| `weather_cache_hits_total` | counter | Cache effectiveness |
| `db_query_duration_seconds` | histogram | Database performance |

### Dashboards (Grafana)

- Request rate, error rate, latency (RED method)
- Pod CPU/memory, HPA replica count
- Weather API failure rate and circuit breaker state

### Tracing (OpenTelemetry)

- Trace full request: Ingress → app → DB → weather.gov
- Identify slow spans (DB vs external API)

### Alerts

| Alert | Condition | Severity |
|-------|-----------|----------|
| High 5xx rate | > 1% for 5 min | page |
| High latency | p95 > 2s for 10 min | warn |
| Pod restarts | > 3 in 15 min | page |
| DB connection errors | any sustained | page |
| Weather API failures | > 50% for 10 min | warn (degraded, not down) |

---

## 9. SLI / SLO

### Service Level Indicators

| SLI | Measurement |
|-----|-------------|
| **Availability** | Ratio of successful (non-5xx) responses to total requests |
| **Latency** | p95 response time for `GET /{state}` |
| **Correctness** | Ratio of valid 2-letter codes returning 200 with bird data |

### Example SLOs

| SLO | Target | Error budget (30-day) |
|-----|--------|----------------------|
| Availability | 99.9% | ~43 min downtime/month |
| Latency (p95) | < 500ms (cached weather) | 0.1% of requests may exceed |
| Latency (p95, cache miss) | < 2s | acceptable for external API call |
| Error rate (5xx) | < 0.1% | |

Weather.gov outages consume **error budget for the weather component only** - bird data availability SLO remains independent if graceful degradation works.

---

## 10. Security

| Area | Production approach |
|------|---------------------|
| **Container** | Run as non-root user (`USER 1000`); read-only root filesystem where possible |
| **Image scanning** | Trivy in CI; block critical CVEs |
| **RBAC** | Least-privilege ServiceAccount; no cluster-admin for app |
| **NetworkPolicies** | Use NetworkPolicies for ingress and internal service egress. For external FQDN egress such as weather.gov, use CNI FQDN policies, an egress proxy, or cloud firewall controls |
| **Secrets** | DB credentials via External Secrets Operator → AWS Secrets Manager / GCP Secret Manager |
| **SQL injection** | Parameterized queries via the PostgreSQL access layer |
| **Input validation** | 2-letter state code regex (already in Module 1); reject unexpected input early |
| **TLS** | End-to-end: client → Ingress (TLS), app → DB (TLS), app → weather.gov (TLS) |

---

## 11. Reliability and disaster recovery

| Concern | Approach |
|---------|----------|
| **Multi-AZ** | Kubernetes nodes and RDS/Cloud SQL across 3 availability zones |
| **Rolling deploys** | `maxUnavailable: 0`, `maxSurge: 1` - zero-downtime deploys |
| **Rollback** | Helm/ArgoCD rollback to previous revision (< 5 min) |
| **DB backups** | Daily snapshots + PITR; restore tested quarterly |
| **DR plan** | Document RTO/RPO (e.g. RPO 1h, RTO 4h); cross-region read replica for critical workloads |
| **Graceful degradation** | Weather unavailable → return bird data with `weather: null`; core API stays up |

---

## 12. Cost / FinOps

| Practice | Detail |
|----------|--------|
| **Right-sizing** | Start small (100m CPU, 128Mi RAM); tune from Prometheus data |
| **HPA** | Scale down during low traffic (nights/weekends if applicable) |
| **Tagging** | Label all resources: `app=birds`, `env=prod`, `team=platform` - enable cost allocation |
| **Cost alerts** | Cloud billing alert at 80% and 100% of monthly budget |
| **Cache** | In-memory TTL cache initially; shared Redis when replica count grows - reduces weather.gov load and latency |
| **Managed services** | RDS/Cloud SQL costs more than self-hosted but saves engineering time; acceptable trade-off |
| **Avoid over-provisioning** | PDB + HPA instead of statically running 20 pods "just in case" |

---

## 13. Production gaps from Module 1

| Module 1 (current) | Production change |
|--------------------|-------------------|
| `flask run` dev server | **gunicorn** with multiple workers |
| SQLite in container | **Managed PostgreSQL** with migrations |
| `birds-app:local` image | Push to **ECR/GCR/ACR** with versioned tags |
| `kubectl port-forward` access | **Ingress** with TLS and DNS |
| No CI/CD | **GitHub Actions + ArgoCD** GitOps pipeline |
| No observability | **Prometheus + Grafana + structured logs** |
| No security hardening | **non-root, NetworkPolicies, secrets manager, image scanning** |
| Basic SQLite data access | **PostgreSQL access layer** with migrations, pooling, and parameterized queries |
| No caching | In-memory TTL cache initially; **shared Redis** when multiple replicas need a common cache |
| Ingress disabled in Helm | Enable Ingress with cert-manager in prod values |

---

## 14. Summary

This design treats the Birds API as a **small but real production service**: stateless and horizontally scalable on Kubernetes, backed by a managed database, resilient to external API failures, observable through standard SRE tooling, and deployed through automated CI/CD with clear rollback paths. The philosophy is to invest in **boring, proven patterns** (managed Postgres, GitOps, HPA, caching) rather than exotic infrastructure, while keeping the application simple enough to operate with a small platform team. Module 1 proved the app works end-to-end; Module 2 describes how to run it safely, observably, and cost-effectively at scale.
