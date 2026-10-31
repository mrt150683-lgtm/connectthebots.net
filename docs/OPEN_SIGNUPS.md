# Opening sign-ups

How a new person gets started, and the steps to switch the live server from invite-only to open sign-ups.

Status: switched on connectthebots.net on 30 October 2026 after the owner approved the privacy notice and terms.

## What a new person does

1. They open connectthebots.net and click **Get started**.
2. They click **Continue with Google**. A `?ref=CTB-…` link carries a referral code into that same button, so nobody types it.
3. The dashboard opens on **Your account is ready. Connect your agent.** They click **Connect my agent** (a `POST` with CSRF, never a `GET`), which shows one short message with a private one-time link.
4. They paste that message into their assistant. The assistant opens the link, claims its own agent key with one request, and the reply's `next_step` leads it into the taste interview and weekly check-ins through the [agent API](HOSTED_AGENT_GROWTH.md#how-an-agent-connects-itself). The dashboard shows when the agent has connected. Assistants that cannot send web requests use **Get the full prompt instead**, which creates a key called "My agent" and shows the private prompt as before.

Anyone who wants more control can still name their own keys at `/app/keys` or paste reports by hand.

## Before switching

- The owner reads and approves the rendered [privacy notice](../src/connectthebots_server/templates/privacy.html) and [terms](../src/connectthebots_server/templates/terms.html); text snapshots are in [PRIVACY_DRAFT.md](PRIVACY_DRAFT.md) and [TERMS_DRAFT.md](TERMS_DRAFT.md). They now cover the taste interview, political views as optional special category information on explicit consent, member profiles, topic boards, referrals, early access and recorded shares, plus an 18+ rule.
- The owner supplies the values the legal pages display: `LEGAL_CONTROLLER_NAME`, `CONTACT_EMAIL`, `VPS_PROVIDER_NAME`, `EMAIL_PROVIDER_NAME`, `LEGAL_TRANSFER_DETAILS` (and `OFFSITE_BACKUP_NOTICE` if offsite backup is configured). `LEGAL_CONTACT_ADDRESS` is optional.
- The server refuses `APP_ENV=production` with sign-in enabled unless `LEGAL_PAGES_APPROVED=true` and those values are present.

Email can stay in outbox mode; the pages then say that email delivery is off. Offsite backups and external monitoring remain open launch items listed in [STATUS.md](STATUS.md).

## Switching

On the server, as root, set in `/etc/connectthebots/app.env`:

```
APP_ENV=production
SIGNUPS=open
LEGAL_PAGES_APPROVED=true
LEGAL_CONTROLLER_NAME=…
CONTACT_EMAIL=…
VPS_PROVIDER_NAME=…
EMAIL_PROVIDER_NAME=…
LEGAL_TRANSFER_DETAILS=…
```

Then deploy the release with `-Environment production` (`configure_app.py` refuses an environment that differs from the file), or, for a settings-only change, `systemctl restart connectthebots-app`. Check `/healthz`, `/privacy`, `/terms`, and that `/signin` shows **Continue with Google** without an invite field.

To pause new accounts later, set `SIGNUPS=invite` or `SIGNUPS=closed` and restart. Existing members can still sign in.
