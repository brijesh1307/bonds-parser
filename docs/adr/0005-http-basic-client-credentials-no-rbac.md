# ADR-0005: HTTP Basic client credentials, no RBAC

**Status:** Accepted

## Context

Callers are a small number of trusted systems: the separate frontend project and integrating
scripts. Access must be controlled by credentials sent in the HTTP header, work from Swagger and
`curl`, and need no identity provider. RBAC and SSO are explicitly out of scope. We still need to
know which client — and, informationally, which person — performed each action.

## Decision

Use **HTTP Basic** with client credentials (baseline D8):

- `Authorization: Basic base64(client_id:client_secret)` on all endpoints except `/health`.
- Secrets hashed with **Argon2** in `clients.secret_hash`; plaintext shown once by the CLI.
- Clients managed only by CLI: `add-client`, `list-clients`, `disable-client`, `rotate-secret` (`CLIENT_ADDED`, `CLIENT_DISABLED`, `CLIENT_SECRET_ROTATED`).
- Any active client may call any endpoint (no roles).
- Optional `X-Actor-Name` header is stored in audit (`actor_name`, `approved_actor_name`) but not trusted for access decisions.
- Brute-force limiter: `BONDS_AUTH_MAX_FAILURES` failures per IP in `BONDS_AUTH_WINDOW_SECONDS` → 429 for `BONDS_AUTH_BLOCK_SECONDS`; `AUTH_FAILED` / `AUTH_BLOCKED` audited.
- Must run over HTTPS in any non-local deployment.

## Consequences

**Positive**
- Trivial for clients; supported natively by Swagger "Authorize", curl, every HTTP library.
- No token issuing, expiry or IdP to operate.
- Argon2 makes leaked hashes costly to crack.

**Negative**
- Credentials sent on every request → TLS is mandatory.
- Argon2 verification per request costs CPU (**Assumption:** a short-lived in-memory cache of successful verifications may be added if it becomes a bottleneck).
- No per-endpoint permissions: any client can approve templates. Accountability relies on audit.
- `X-Actor-Name` is self-declared.

## Alternatives considered

| Option | Why not |
|---|---|
| OAuth2 / OIDC with JWT | Needs an IdP; SSO is out of scope. |
| API key header (`X-API-Key`) | Similar security, but less standard tooling and no Swagger-native username/password split. |
| mTLS | Strong, but certificate management overhead for MVP. |
| RBAC (reviewer / approver roles) | Out of scope; can be layered on `clients` later. |
