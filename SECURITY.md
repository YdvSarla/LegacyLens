# Security

## Reporting a problem

Please do not open a public issue for a security problem. Use
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository, and include the steps to reproduce it.

## What LegacyLens treats as untrusted

Repositories, uploaded archives, AI output and generated tests can all be hostile. The server is
designed so that analyzing a repository never runs its code. Code only runs in the test sandbox,
and only when a person starts a test.

## Protections

| Area | Protection |
| --- | --- |
| Test execution | Throwaway containers with no network, a read-only root, size-limited memory disks, no Linux capabilities, a non-root user, and limits on memory, CPU, processes, files and time. Logs are off, so output cannot fill a disk. |
| Docker access | Tests run in a nested engine that requires client certificates and sits on its own network. The host's Docker socket is never mounted. |
| Dependency install | Optional and off by default. Install scripts are disabled, and the tests then see the result as read-only. |
| ZIP upload | Path traversal, symlinks, decompression bombs and embedded `.git` directories are refused or dropped. A bad update leaves the existing code in place. |
| GitHub import | Only `https://github.com/owner/repo` is accepted, which also blocks requests to internal hosts. Tokens are used once through the environment and never stored. |
| Git | Hooks and config-driven commands are disabled, and commit names are validated. |
| Secrets | Files that look like secrets are never indexed, shown, sent to AI or copied to the sandbox. Obvious secrets inside other source files are removed from prompts. |
| Accounts | Argon2 password hashes, HttpOnly SameSite cookies, sign-in throttling, and session revocation when a password changes. |
| Cross-site requests | Writes that rely on the session cookie must come from the same origin. |
| Authorization | Every project route checks ownership, and returns "not found" for other people's projects. |
| Browser | Content Security Policy, frame protection, no raw HTML in rendered Markdown, and redirects limited to this site. |
| AI | Only free tiers and local models can be configured. Prompts mark repository content as untrusted, and citations are checked against the code that was retrieved. |

## Known limits

- **The nested Docker engine runs in privileged mode.** A container escape would land inside that service, not directly on the host, but it is the main remaining risk. On a shared server, run LegacyLens on its own machine or VM.
- **Dependency install has network access.** It cannot reach the database, but it can reach the internet and the backend's public API. Leave it off for code you do not trust.
- **Secret removal is pattern based.** It catches common forms, not all of them. For code you may not share, turn on **Use local AI only** in the project settings.
- **Prompt injection cannot be fully prevented.** A repository can try to mislead the AI into a wrong answer. The AI has no tools and cannot act, so the damage is limited to that answer.
- **There is no audit log or admin role.** Every account is equal, and all accounts share the server's AI quota.

## Before exposing it to a network

1. Serve it over HTTPS, and set `COOKIE_SECURE=true`.
2. Set `FRONTEND_BIND=0.0.0.0`, and `CORS_ORIGINS` to your public address.
3. Set `ALLOW_REGISTRATION=false` once your accounts exist.
