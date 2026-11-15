# Deploying ShroudX to `https://shroudx.us`

This repository is preconfigured to serve both the React SPA (`/`, `/assets/*`), legacy dashboard (`/legacy`), and FastAPI backend (`/api/*`, `/health`) from a single origin on `https://shroudx.us` (with `www.shroudx.us` redirecting `301` to `https://shroudx.us` when `SPHX_CANONICAL_HOST=shroudx.us` is set).

Because `shroudx.us` uses **Registrar DNS** at the **root (apex) domain**, **Fly.io** and **Render** are recommended over platforms that only provide a `CNAME` target (most registrars do not support standard `CNAME` records on the root `@` record unless they offer proprietary `ALIAS`/`ANAME` flattening).

---

## Option A: Fly.io (Recommended — 1 GB RAM + native `A`/`AAAA` apex records)

`fly.toml` is included at the repository root.

1. **Install & log in to `flyctl`**, then create and deploy the app from inside `ShroudX/`:
   ```powershell
   # Install on Windows (PowerShell):
   winget install Fly-io.flyctl

   # Refresh PATH in the current PowerShell session (or open a new terminal):
   $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")

   cd C:\Users\lukit\sphereX\ShroudX
   flyctl auth login
   # If "shroudx" is taken globally on fly.dev, change `app = "..."` in fly.toml first.
   flyctl apps create shroudx
   flyctl ips allocate-v4 --shared
   flyctl ips allocate-v6
   flyctl deploy --ha=false
   ```

2. **Issue TLS certificates for `shroudx.us` and `www.shroudx.us`**:
   ```powershell
   flyctl certs create shroudx.us
   flyctl certs create www.shroudx.us
   flyctl certs show shroudx.us
   flyctl ips list
   ```
   `flyctl ips list` and `flyctl certs show shroudx.us` give the exact IPv4 (`A`), IPv6 (`AAAA`), and ACME/CNAME values to enter at your registrar.

3. **Add DNS records at your registrar** (Namecheap / Porkbun / GoDaddy / Route 53):
   | Type | Host / Name | Value | Notes |
   | --- | --- | --- | --- |
   | `A` | `@` | `<Fly IPv4 from flyctl ips list>` | Serves `https://shroudx.us` |
   | `AAAA` | `@` | `<Fly IPv6 from flyctl ips list>` | IPv6 support for `shroudx.us` |
   | `CNAME` | `www` | `<your-app>.fly.dev` | Redirects `www.shroudx.us` -> `https://shroudx.us` |
   | `CNAME` | `_acme-challenge` | *(from `flyctl certs show shroudx.us`, if shown)* | Optional DNS-01 TLS validation |

   *(Remove any existing registrar parking/forwarding `A` or `CNAME` records for `@` or `www`.)*

---

## Option B: Render (`render.yaml` Blueprint)

`render.yaml` is included at the repository root.

1. Push your branch to GitHub (`origin/main`), then in the **Render Dashboard** choose **New + -> Blueprint** (or **New + -> Web Service** with **Runtime: Docker**, Health Check Path `/health`, and env vars `SPHX_MODE=auto`, `SPHX_CANONICAL_HOST=shroudx.us`).
2. In your Render service go to **Settings -> Custom Domains** and add:
   - `shroudx.us`
   - `www.shroudx.us` (Render can also redirect `www` -> apex at the edge)
3. **Add the exact DNS records Render displays in Custom Domains** at your registrar:
   | Type | Host / Name | Value |
   | --- | --- | --- |
   | `A` | `@` | `<Render static IP shown in Custom Domains>` |
   | `CNAME` | `www` | `<your-service>.onrender.com` |

---

## Verify live deployment

Once DNS propagates and the platform marks the TLS certificate as issued:

```powershell
python scripts/smoke_test.py --url https://shroudx.us
```

Expected checks:
- `https://shroudx.us/health` returns `200` (`status: "ok"`, `data_mode: "auto"`).
- `https://www.shroudx.us/` redirects `301` -> `https://shroudx.us/`.
- `https://shroudx.us/robots.txt` disallows `/api/` crawlers.
- `https://shroudx.us/` serves the built React app and `/legacy` serves the original HTML dashboard.
