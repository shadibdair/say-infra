# Birds App — Sayari Infrastructure Take-Home (Module 1)

## Project overview

This repository contains a Flask API, SQLite database, Dockerfile, and Helm chart for the Sayari Infrastructure technical challenge. The starter project had broken application logic, no container build, and a Helm chart that deployed nginx instead of the birds application. Module 1 fixes those issues and demonstrates end-to-end deployment: local Python, Docker, and Kubernetes (kind + Helm).

```
.
├── app.py
├── birds.db
├── requirements.txt
├── Dockerfile
└── helm/birds/
```

## What the application does

The API returns state bird facts from `birds.db` and active weather alerts from [weather.gov](https://api.weather.gov) for a given US state abbreviation.

| Endpoint | Description |
|----------|-------------|
| `GET /` | Help message |
| `GET /health` | Health check for Kubernetes probes |
| `GET /{state}` | Bird data + weather alerts (e.g. `/CA`) |

Invalid state codes (not exactly 2 letters) return HTTP 400. If the weather API fails, bird data is still returned.

## Prerequisites

| Tool | Purpose |
|------|---------|
| **Python** 3.9+ | Run the app locally |
| **Docker** | Build and run the container image |
| **kind** | Local Kubernetes cluster |
| **kubectl** | Interact with the cluster |
| **Helm** 3.x | Deploy the application chart |

Install references: [Python](https://www.python.org/downloads/) · [Docker](https://docs.docker.com/get-docker/) · [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation) · [kubectl](https://kubernetes.io/docs/tasks/tools/) · [Helm](https://helm.sh/docs/intro/install/)

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export FLASK_APP=app.py
flask run --port 5000
```

> On macOS, port 5000 may be used by AirPlay Receiver. Use `--port 5001` if needed.

Test:

```bash
curl -i http://127.0.0.1:5000/
curl -i http://127.0.0.1:5000/health
curl -i http://127.0.0.1:5000/CA
curl -i http://127.0.0.1:5000/NY
curl -i http://127.0.0.1:5000/XXX   # expect 400
```

## Docker build and run

```bash
docker build -t birds-app:local .

docker run -d --name birds-app -p 5001:5000 birds-app:local
```

The container listens on port **5000**. Host port **5001** is used in the example to avoid macOS conflicts on 5000.

Test:

```bash
curl -i http://127.0.0.1:5001/
curl -i http://127.0.0.1:5001/health
curl -i http://127.0.0.1:5001/CA
curl -i http://127.0.0.1:5001/NY
curl -i http://127.0.0.1:5001/XXX   # expect 400
```

## Kubernetes deployment (kind + Helm)

### Create cluster

```bash
kind create cluster --name birds-dev
kubectl cluster-info --context kind-birds-dev
```

### Build image and load into kind

kind nodes cannot pull images from the host Docker daemon. Build locally, then load:

```bash
docker build -t birds-app:local .
kind load docker-image birds-app:local --name birds-dev
```

### Lint and deploy

```bash
helm lint ./helm/birds
helm template birds ./helm/birds

helm upgrade --install birds ./helm/birds \
  --namespace birds \
  --create-namespace
```

### Verify

```bash
kubectl get pods,svc -n birds
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=birds -n birds --timeout=60s
kubectl logs -n birds -l app.kubernetes.io/name=birds
```

## Port-forward and test

```bash
kubectl port-forward -n birds svc/birds 8080:80
```

In a second terminal:

```bash
curl -i http://127.0.0.1:8080/
curl -i http://127.0.0.1:8080/health
curl -i http://127.0.0.1:8080/CA
curl -i http://127.0.0.1:8080/NY
curl -i http://127.0.0.1:8080/XXX   # expect 400
```

Service port **80** forwards to container port **5000**.

## Cleanup

```bash
# Stop port-forward with Ctrl+C

helm uninstall birds -n birds
kind delete cluster --name birds-dev
docker rm -f birds-app
```

## Summary of fixes

**Application (`app.py`)**

- Weather API URL bug fixed — state code is now substituted correctly in the weather.gov request
- State code validation added — only 2-letter codes accepted; input normalized to uppercase
- Safe Weather API error handling added — timeout, graceful failure, bird data returned if weather is unavailable
- `/health` endpoint added for Kubernetes probes

**Container**

- `Dockerfile` added — `python:3.12-slim`, installs `requirements.txt`, copies `app.py` and `birds.db`, Flask on `0.0.0.0:5000`

**Helm chart (`helm/birds/`)**

- Chart changed from nginx to `birds-app:local` with `imagePullPolicy: IfNotPresent`
- Container port changed to **5000**
- Service `targetPort` changed to **5000** (Service port remains 80)
- Liveness and readiness probes changed to `/health`
- `appVersion` corrected from `1.16.0` to `1.0.0`

## Known limitations

- **SQLite** is acceptable for this take-home/local demo, but not suitable for production scale (multi-replica writes, backups, HA)
- **Flask dev server** (`flask run`) is acceptable for the exercise; production should use gunicorn/uwsgi behind a proper ingress
- **weather.gov** is an external dependency; production would need retry logic, caching, circuit breaking, and monitoring
- **birds.db** may contain pre-existing data inconsistencies (e.g. incorrect state names for some abbreviations)
- Chart uses a local-only image tag (`birds-app:local`); production requires a container registry and CI/CD pipeline
- Ingress is disabled; access is via `kubectl port-forward`

## Module 2 — Production design

The production-scale architecture and operational design is documented in [PRODUCTION_DESIGN.md](./PRODUCTION_DESIGN.md).

It covers Kubernetes production setup, CI/CD, managed database migration, observability, security, reliability, external dependency handling, and cost/FinOps considerations.
