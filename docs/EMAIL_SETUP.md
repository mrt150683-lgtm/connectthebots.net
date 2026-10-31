# Transactional email setup

Status, 30 October 2026: Brevo domain authentication, the verified sender and protected SMTP settings are prepared. One separately approved message arrived with passing SPF, aligned DKIM and DMARC; the exact application unsubscribe headers survived, and the unsubscribe/restore check passed. Brevo converted the plain-text payload to HTML and added an image. The owner chose to keep Brevo with anonymous measurement disclosed. Routine delivery remains off (`EMAIL_MODE=outbox`) pending the final notice and launch approval. See [implemented behavior](HOSTED_NOTIFICATIONS.md) and the [launch record](HOSTED_V1_PROGRESS.md).

The owner delegated selection of a free sending service on 30 October 2026. **Brevo Free** is the initial choice: SMTP support, 300 sends per day, no payment card or time limit. Its free plan includes provider branding. The allowance is a daily cap, not a promise that all notifications will fit. Pending transactional mail beyond the cap is subject to a finite provider queue. Keep application delivery records and surface failed delivery; never treat queueing as receipt by a person. Recheck limits before launch. [Plan](https://help.brevo.com/hc/en-us/articles/208589409-About-Brevo-s-pricing-plans), [limits](https://help.brevo.com/hc/en-us/articles/208580669-FAQs-What-are-the-limits-of-the-Free-plan).

## Owner setup and DNS

1. Create a free account using the owner's chosen mailbox. The owner reviews the provider terms and any required agreement. Do not select a paid plan, add-on, dedicated IP or marketing campaign. Keep actual contact details outside Git.
2. Confirm transactional sending is enabled. Brevo may require separate activation through support. Sending a support request needs the owner's explicit instruction. [Activation and troubleshooting](https://help.brevo.com/hc/en-us/articles/115000188150-Troubleshooting-Issues-with-Brevo-SMTP).
3. Add the existing sending domain and choose manual authentication so the exact DNS changes can be reviewed. Copy the verification and DKIM records shown for that account; never invent record values. Preserve the website's existing A/CNAME records and mail routing.
4. Inspect existing SPF and DMARC first. Preserve the existing `p=quarantine` policy; do not add a second DMARC or SPF record. Brevo's shared infrastructure normally authenticates its own envelope sender for SPF, while aligned DKIM authenticates the visible sender domain. Add SPF changes only if the provider's actual setup requires them. Do not change MX records merely to enable sending. Confirm the resulting DKIM and DMARC alignment with a delivered message. [Domain authentication](https://help.brevo.com/hc/en-us/articles/12163873383186-Authenticate-your-domain-with-Brevo-Brevo-code-DKIM-DMARC), [domain setup](https://help.brevo.com/hc/en-us/articles/35852083084178-Domain-setup-for-better-email-deliverability).
5. Use the owner's chosen sender address on the authenticated domain. SMTP sending does not itself provide an inbox. The current read-only DNS check found no apex MX or SPF record; the existing DMARC policy is `p=quarantine`. Create an incoming support mailbox or forwarding route through the owner's selected mail service, review the actual required DNS records, and verify receipt of support messages and replies. Keep actual mailbox details private. No DNS was changed by this work.

## Protected application configuration

For an explicitly approved SMTP activation, the owner enters the credentials directly into `/etc/connectthebots/app.env` (root-owned, mode 0600). Never paste credentials into chat, a PR, a command argument or logs.

- `SMTP_HOST=smtp-relay.brevo.com`
- `SMTP_PORT=587`
- `SMTP_STARTTLS=true`
- `SMTP_USERNAME`: the login shown in Brevo's SMTP settings
- `SMTP_PASSWORD`: a dedicated SMTP key, not the account password or API key
- `SMTP_FROM_ADDRESS`: the verified sender
- `EMAIL_PROVIDER_NAME=Brevo`
- `EMAIL_PROCESSING_NOTICE`: reviewed plain text describing provider measurement and retention, rendered under the named provider in the privacy notice. It is shown in outbox previews too, so phrase it conditionally on delivery being enabled. It does not switch on sending or approve the notice. Never put credentials or private account details in it.

Keep `EMAIL_MODE=outbox` during development. Current configuration deliberately refuses SMTP mode outside production; test transport through SMTP protocol doubles and an injected queue adapter and review a narrowly scoped, explicitly approved real delivery check before enabling sending. Do not weaken that protection to make a test pass. [SMTP settings](https://help.brevo.com/hc/en-us/articles/115000188150-Troubleshooting-Issues-with-Brevo-SMTP).

## Worker and local verification

Deployment installs a hardened `ctb` oneshot service and `connectthebots-notifications.timer`. A tick runs `python -m connectthebots_server.notifications tick --limit 20`, beginning about two minutes after boot and repeating about a minute after completion. The CLI returns counts, never recipients, tokens, bodies or credentials. Do not run manual ticks against an SMTP-enabled production environment without approval to send the queued mail.

Outbox mode stores generic test payloads without opening a transport. Existing outbox/legacy records are not automatically replayed when SMTP is enabled. Local fake-transport tests cover TLS, retry, concurrency, recipient freshness, suppression and uncertain acceptance. They do not establish actual Brevo behavior or inbox receipt. Mail states are described in [HOSTED_NOTIFICATIONS.md](HOSTED_NOTIFICATIONS.md).

## Release evidence still required

Record provider activation, authenticated DNS, the approved recipient and successful delivery, SPF/DKIM/DMARC results, unsubscribe behavior and a failed-send/retry check without logging message bodies or credentials. Disable tracking where supported and disclose any provider processing/retention in the final privacy text. Confirm provider locations and transfer safeguards from the actual account agreement. Production deployment, legal-page approval and sending real test emails remain separate approval steps.

Brevo automatically adds a transactional `List-Unsubscribe` header. Its published guidance does not confirm that a supplied application header survives unchanged or that DKIM signs both `List-Unsubscribe` and `List-Unsubscribe-Post`. Inspect the received raw message before activating production SMTP: the one-click endpoint must disable exactly the intended notification type. Do not substitute an undocumented header or claim that SMTP acceptance proves inbox delivery. [Brevo header behavior](https://help.brevo.com/hc/en-us/articles/19100260472850-FAQs-About-list-unsubscribe-and-list-help-headers-in-emails), [one-click specification](https://www.rfc-editor.org/rfc/rfc8058.html#section-3.1).

In provider settings, enable per-contact tracking consent and disable tracking for unknown consent. Also select **Yes** for transactional **Anonymous email tracking** so future open/click events are not associated with individual contacts in the provider's statistics. Do not import app users into marketing lists. These settings do not remove the tracking image or make delivery anonymous. Choose the shortest documented transactional log retention (one month) and explicitly select **Never store previews**, then verify the settings in the actual account. [Consent controls](https://help.brevo.com/hc/en-us/articles/37114679474706-About-email-tracking-pixels-and-the-CNIL-recommendation-in-Brevo), [anonymous statistics](https://help.brevo.com/hc/en-us/articles/11643306229906-Can-I-anonymize-the-tracking-of-opens-and-clicks-for-my-emails), [retention controls](https://help.brevo.com/hc/en-us/articles/4415743225746-Configure-a-custom-retention-period-for-your-transactional-logs-and-email-previews).

## Launch disclosure draft

Use the following plain-text disclosure only after verifying the corresponding account settings. The final rendered notice still needs owner approval; the provider choice alone does not approve every legal statement.

> When email delivery is enabled, Brevo sends our notifications. Its emails include a small remote image for aggregate open measurement and may measure link clicks. We use anonymous measurement settings: those open and click statistics are not linked to individual contacts in our Brevo reports. This does not make delivery anonymous. Brevo receives your destination address, email contents and technical delivery information to send the message. We do not collect consent for individual open or click tracking. Our account is configured to keep transactional logs for one month and not to store email previews; scheduled log deletion can take up to a further 24 hours. This provider retention is separate from the application records described in this notice.

Brevo's [July clarification](https://developers.brevo.com/changelog/2026/7/21) distinguishes anonymized aggregate opens from removal of the image. The approved received-message test established the image's presence; a relay acceptance or a checkbox alone cannot establish its absence. No further email is authorized by the completed one-message test.
