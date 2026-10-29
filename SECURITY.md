# Security policy

## Intended deployment

Paper Research Agent is a single-user local application. The API binds to `127.0.0.1` by default. HTTP and WebSocket endpoints have no user authentication or authorization, and CORS is not an authentication mechanism. Do not expose the service directly to an untrusted network or share one logged-in model account with untrusted users. Network deployment requires separately designed authentication, authorization and isolation.

The code is an early release. Test changes against the current source before reporting an issue; no long-term support branch or response-time guarantee is currently defined.

## Credentials and research data

Codex mode runs under the backend's operating-system user and reuses the login managed by Codex CLI. The application does not need a copy of the CLI authentication file. Keep credentials outside the repository, release archives, container images and diagnostic attachments. API-provider keys belong in a local environment or secret store, never in committed files.

Research topics, prompts and selected paper excerpts are sent to the configured model provider. Search queries are sent to retrieval services. Local storage therefore does not mean all processing is offline; use only materials you are permitted to send to those services. Review the applicable provider's data policies for your account and deployment.

Task snapshots, original abstracts/full text, review drafts and prompt logs can contain unpublished research or private documents. Git ignore rules are only one protection: already-tracked files and old commits remain publishable until explicitly addressed. Review the actual files in every release archive and do not publish the working directory as a blanket upload. Project licensing does not cover third-party papers or user-imported templates.

## Reporting a vulnerability

Use the repository host's private vulnerability reporting feature if it is enabled. If no private channel is available, open a minimal issue requesting a private contact method without including exploit details, credentials or sensitive data. Do not send credentials as proof, even in a private report.

Useful details are the affected commit, a synthetic reproduction, the observed impact, and sanitized environment information. Avoid attaching `.env`, authentication JSON, complete task directories or raw model-call logs.

If credentials have already been disclosed, revoke or rotate them with their provider first. Removing a file from the current tree does not remove it from Git history or previously distributed copies.
