# Sharing with an invited tester through Cloudflare Tunnel (Phase B)

This exposes the app at a stable hostname, protected by Cloudflare Access, without opening any port on your router.

Operational limits to be aware of:
- Your computer must stay **powered on, awake, online, and running Docker and the tunnel**. The tunnel provides connectivity; it does not host the app.
- Traffic passes through Cloudflare.
- In AI mode, the relevant context also goes to Anthropic.

Do not tell testers that their data stays entirely on your machine.

**You need:**
- a Cloudflare account (free plan is fine),
- a domain whose DNS is on Cloudflare (registering a domain usually costs money, even though the tunnel itself is free),
- the email addresses of the owner and the tester.

## 1. Cloudflare account and domain

Create or sign in to a Cloudflare account. Add a domain you own, or use an existing one, and follow Cloudflare's steps to point its nameservers at Cloudflare. Choose a hostname for the app, e.g. `assistant.example.com`.

## 2. Create an Access application for the whole hostname

In **Cloudflare Zero Trust → Access → Applications → Add an application → Self-hosted**:

- **Application domain:** `assistant.example.com`, with no path. That covers everything, including `/api`, uploads, exports and job polling.
- **Session duration:** your choice, e.g. 24 hours.
- Note the **Application Audience (AUD) tag** and your **team domain** (`https://<team>.cloudflareaccess.com`).

## 3. Allow only the two email addresses

Add a policy:
- **Action:** Allow
- **Include → Emails:** the owner's and the tester's addresses
- **Login method:** one-time PIN by email, or an identity provider you've configured

Do **not** add an "Everyone", bypass or service-auth rule. Anyone not listed is denied by default.

## 4. Create a named tunnel to the gateway

In **Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared**, name it `projections-assistant`.

- Skip the install instructions: the tunnel runs in Docker.
- Copy the **tunnel token** into a file only you can read:
  ```bash
  mkdir -p secrets
  printf '%s' 'PASTE-TOKEN-HERE' > secrets/cloudflare_tunnel_token
  chmod 600 secrets/cloudflare_tunnel_token
  ```
- Add a **public hostname**: `assistant.example.com` → service **HTTP**, URL `gateway:8080`. The service is reached inside the private Compose network.

The tunnel token and the Anthropic API key are different secrets. The token goes only to `cloudflared`, and the API key goes only to the backend.

## 5. Configure the backend to verify Access tokens

Edit `.env`:

```dotenv
DEPLOYMENT_PROFILE=remote
AUTH_MODE=cloudflare_access
ALLOWED_ORIGIN=https://assistant.example.com
CF_ACCESS_TEAM_DOMAIN=https://<team>.cloudflareaccess.com
CF_ACCESS_AUDIENCE=<AUD tag from step 2>
ALLOWED_EMAILS=owner@example.com,tester@example.com
OWNER_EMAIL=owner@example.com
```

On every request, the backend verifies the `Cf-Access-Jwt-Assertion` token:
- **Signature:** checked against your team's published signing keys at `/cdn-cgi/access/certs`, with the PyJWT library.
- **Claims:** issuer, audience and expiry must be valid.
- **Email:** must be on `ALLOWED_EMAILS`.

It then maps the email to a local user. Each user sees only their own workbooks. A raw email header is never trusted, and the remote profile refuses to start with the local developer identity. Being on the Docker network is not treated as proof of who someone is.

Check the configuration before starting. The check needs `uv` on the host:

```bash
uv run python -m app.ops.readiness --env-file .env --token-file secrets/cloudflare_tunnel_token
```

It must print `Ready`. It rejects:
- missing Access settings,
- the local auth bypass,
- a non-https or localhost origin,
- an owner email that isn't on the allowlist,
- world-readable secret files,
- missing AI budgets in anthropic mode.

## 6. Start and verify from another device

```bash
docker compose --profile remote up -d --build
```

From a **different browser or device**:

1. Open `https://assistant.example.com`. You should get the Cloudflare login, receive a code by email, and then see the app.
2. Sign in as the tester and confirm they cannot see the owner's workbooks or the Usage page.
3. In a private window with no login, open `https://assistant.example.com/api/workbooks` and an export URL. Both must be blocked by Access.
4. Try an email address that isn't on the list: Access must refuse it.

The automated tests verify the backend's rejection of missing, expired, wrong-audience, wrong-issuer, wrongly-signed and disallowed tokens using mocked keys. The Cloudflare side (steps 1–4 and 6) is manual and was not exercised by the test suite.

## 7. Stop sharing or rotate the token

- **Stop sharing** while keeping local use:
  ```bash
  docker compose --profile remote stop cloudflared
  ```
  Or delete the Access application or the tunnel in the dashboard.
- **Rotate the token:**
  1. In **Tunnels → (your tunnel) → Refresh token**, generate a new token.
  2. Write it to `secrets/cloudflare_tunnel_token`.
  3. Run `docker compose --profile remote up -d cloudflared`.
- **Remove a tester:** take them off the Access policy *and* off `ALLOWED_EMAILS`, then restart the backend.

No inbound port forwarding is needed at any point.

## Quick Tunnels

Cloudflare "Quick Tunnels" (random `trycloudflare.com` URLs) are **not** a substitute for Access. A hard-to-guess URL is not authentication, and this app's own login relies on Access. Don't use Quick Tunnels for this application.
