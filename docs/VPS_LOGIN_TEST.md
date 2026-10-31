# VPS owner login test

30 October 2026. The owner explicitly authorized deployment to the existing VPS and subsequently confirmed successful Google login. The hosted application is served at `https://connectthebots.net` for this limited test. This is not a public-signup launch or approval of the draft trust pages.

## Deployed state

- Release `1fd429128e3acda9f27898a8`, wheel SHA-256 `9991733868f413d8d1f811ca533cf1e3d52b7089d382849a0dfdf29e73ecb027`. All 84 server files and nine migrations match the reviewed local package. Its implementation commit `d36487c` passed [CI run 41065077747](https://github.com/mrt150683-lgtm/AgentsMatch/actions/runs/41065077747).
- `APP_ENV=staging`, HTTPS `PUBLIC_BASE_URL`, sign-in enabled, `SIGNUPS=invite`, `EMAIL_MODE=outbox`, `LEGAL_PAGES_APPROVED=false`. Google credentials were transferred from the owner-selected private file into root-owned mode-0600 `/etc/connectthebots/app.env`; their values were neither printed nor committed.
- One one-day invitation was created for the owner's first account. Private instructions are in ignored `local-data/OWNER-LOGIN.txt`, restricted to the owner's Windows account and SYSTEM. No invitation code belongs in tracked documentation or PR text. Existing accounts can subsequently sign in without a new code.
- Controller name/address remain unconfigured. The role contact is configured, but inbox receipt and the final legal arrangement remain unverified. The security contact endpoint returns 503 until a verified mailbox and reviewed expiry are configured.
- The existing TLS certificate, certificate renewal configuration, HTTP redirects, HSTS, stats and unrelated server settings were preserved. The HTTPS www host now redirects to the canonical apex. No DNS, SSH key/access setting, firewall or repository-visibility change was made.
- A private server-side rollback copy of the original site and nginx files was taken before activation. Its location is in ignored `artifacts/VPS-DEPLOYMENT.json`; do not publish private operational paths or copy the protected environment into Git.

## Observed checks

The owner reported successful login through the real Google provider on 30 October. This establishes owner-reported real-provider login success in addition to the automated initiation checks below. Logout/relogin, a second person's signup and the remaining hosted feature smoke checks have not yet been confirmed. The invitation/code/continuation page is specific to the current restricted test: `SIGNUPS=open` already renders a direct **Continue with Google** link without an invite form. No public-access setting was changed by this confirmation.

Twenty public TLS-verified checks passed: healthy minimal `/healthz`, required security headers, HTTPS/canonical redirects, real invite form, secure host-cookie attributes, authenticated dashboard boundary, bearer-only report identity, labelled example, and Google authorization initiation with the exact callback/basic scopes/PKCE/state/nonce. This did not complete Google authentication or create an account. Evidence: `artifacts/vps-login-verification.json`.

The installed process uses the reviewed release, with the settings above confirmed without exposing secrets. The first encrypted backup service run returned success/exit 0. Evidence: `artifacts/vps-runtime-status.txt`. This is a local VPS backup, not off-server disaster recovery. Notifications remain outbox-only.

The first health probe immediately after nginx reload reached the old static route and returned 404. Subsequent remote and public checks returned 200; the local deployment helper now waits for readiness after reload. A smoke-test assumption about relative redirect URLs was corrected after observing nginx's valid absolute redirect. Neither required an application change.

## Remaining

Owner Google login is confirmed; sign-out/relogin and a second person's signup remain to be recorded. Public launch still needs actual mail and incoming support inbox verification, accurate approved trust pages, off-server storage and trusted current deletion authority, external health monitoring/operator alerts, release review/merge with green `main`, and the remaining two-account/report/email/deletion smoke tests. Open signups and public repository visibility remain later readiness decisions.

The documentation-only head `461d1c1` had one browser pagination assertion failure in [CI run 41065204033](https://github.com/mrt150683-lgtm/AgentsMatch/actions/runs/41065204033); all 404 server tests and the dependency audit passed. It has identical runtime bytes to the green implementation. The failure was not reproduced locally. The test now waits for the exact older-page URL and records separate count/text assertions; 168 scoped Windows checks passed. Follow-up [CI run 41068560890](https://github.com/mrt150683-lgtm/AgentsMatch/actions/runs/41068560890) passed all nine jobs at `ac6bc85`; this is not a claimed production bug fix. See `artifacts/security-ci-pagination-review.md` and [the remaining-work record](HOSTED_V1_PROGRESS.md).
