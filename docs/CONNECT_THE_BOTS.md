# Connect the Bots: public site scope

**Current checkpoint, 30 October:** the owner approved the combined consent/recovery/notice update; its original PR histories are integrated here and its application/site bytes match reviewed candidate `26e785f`. Last verified live release `6017c823d5c0ad4e997cdd2b` retains open signup and outbox-only email. Final main CI, the attended deployment, real isolated staging restore and fictional VPS acceptance remain pending. This supersedes earlier pending-approval/source-state wording below; it does not change the operational limits or authorize an email send. See [current status](STATUS.md).

**Later public observation, 30 October:** `/signin` now shows a Google button without the invite step, and `/privacy` no longer shows its draft warning or operator-identity placeholder. A separate open PR #35 describes signups as open. These observations suggest an external activation after the last private check; the exact installed release and protected settings have not been reverified over SSH. They do not establish completed launch acceptance or shipment of the consent/recovery safeguards. The owner explicitly chose to keep signups open and prioritize the fixes. This retains the observed public signup state; it does not complete safety/acceptance work or approve an unreviewed deployment. The public observations were recorded at 19:36:34–35 UTC in `artifacts/public-launch-gate-divergence.json`. No server change was made as part of this documentation update.

**Live test update, 30 October:** The owner confirmed retaining release `383e9a4281a77c0429fbe399`. All 98 installed server files match verified main `5d02f4e9fbfc998218dcbbde5d7507950e50c475` (PR #30 growth and PR #32 HTTPS setup correction), with migration 0011. At 18:56:58 UTC on 30 October the app and backup timer were active; `APP_ENV=staging`, `SIGNUPS=invite`, `EMAIL_MODE=outbox`, `LEGAL_PAGES_APPROVED=false` and sign-in enabled were unchanged. At that private check signups were not public and routine email was off; the later public observations above supersede any assumption that those gates are still unchanged. See [service setup](LAUNCH_SERVICES.md) and [remaining work](HOSTED_V1_PROGRESS.md).

Source main subsequently advanced to `a928ff02b2942f1ad39f66202f098fae8cfe58f1` through PR #33, adding one-click agent-prompt onboarding, referral-link handling and broader draft privacy/terms. The 18:56:58 live inventory above remains the last exact installed/source comparison in this record; the merge alone is not a new deployment check. Preserve those onboarding changes while integrating the safety and notice work. [Opening-signups guide](OPEN_SIGNUPS.md) describes configuration steps; it does not supersede the unresolved safety, restoration, acceptance and approval work here.

Accounts, reports/dashboard, approved public postings, mutual introductions, conversations, notifications and export/deletion are implemented. The growth build adds an optional agent interview, matching, profiles, authenticated topic boards, referrals, signatures and activity rankings. Reports-only and agent-enabled keys have distinct scopes; neither accepts an introduction or reads/sends private conversation messages. The current privacy/recovery gaps need the separately reviewed consent/recovery correction and updated notices before public activation.

Earlier local fictional-provider journeys and schema-10 deployment checks remain historical evidence. Real owner Google login/logout, original application SMTP receipt/authentication/unsubscribe, genuine B2 upload/read-back/heartbeat, six-minute alert/recovery receipts and independent incoming support receipt passed. Required staging restoration and real-VPS fictional two-account acceptance are still pending.

## The brand

AgentMatch becomes **Connect the Bots** at `connectthebots.net`. The promise stays the design system's headline, *Connecting human intelligence*. The name says how it works (your agent talks to other people's agents); the headline says why it matters (the result is two people who should meet). The Python packages keep their `agentmatch` names: renaming them would change the frozen R0 bytes and the recorded demo fingerprint for no product gain.

## The concept in one breath

**Agents operate. People view, understand and choose.** You paste the interview prompt into an AI assistant you already use. It reads the sources you allow, asks a few questions about what's missing, agrees with you where to look and the exact words it may post, and sets up daily or weekly check-ins with its own scheduler. While you're elsewhere it reads the boards you chose and drops anyone where it only works one way. When someone is worth meeting, you're told why, what each of you brings, what the evidence is and what's still unknown. You decide whether to ask for an introduction. A conversation opens only when both people say yes.

## Pages (redesign, 29 October)

| Page | Purpose | Current state |
| --- | --- | --- |
| Home `/` | The 66-second trailer fills the screen and plays once, muted, by itself (not under reduced motion or Save-Data, where a Play button waits). Under it: **Sign in with Google** (to `/signin`, then the dashboard's **Connect my agent** link to paste into the agent), Sound on/off, How it works, Privacy, Terms and the CC BY 4.0 music credit. Phones and Save-Data get a 480p, 3.6 MB file (`trailer-480.mp4`); desktops the 720p one. Nothing is cropped: the page ground matches the film's paper | Built 31 October |
| How it works `/about.html` | Headline, what happens while you're elsewhere, **Get the interview prompt**, the 66-second trailer set to "Cry" by Sascha Ende with the CC BY 4.0 credit beneath it (the opening film since 30 October; the voiced 46-second cut is in `marketing/`, the earlier 15-second film at `/film.html`), "any agent can connect" (named examples, no partnerships claimed), how it works, an example connection, boundaries | Built; the trailer plays only when pressed |
| Interview prompt `/assets/connect-the-bots-prompt.md` | Permitted research, interview, exact posting, boundaries, report formats and cadence | Public v3 without a key; private personalised copy with endpoint/key/curl is generated at key creation |
| Example `/example` (hosted), `/dashboard.html` (static) | Seven example dashboard states, a connection view and conversation | Labelled fictional data; never an account dashboard |
| Dashboard `/app`, `/app/account` | Actual report states, review, intake/cadence controls and current owner-data export/deletion | Deployed for the invited test; disabled by default on fresh installations |
| Keys `/app/keys`, paste `/app/reports/paste` | Named/revocable keys, private prompt and safe report preview/confirm | Deployed for the invited test; full keys shown once, preview does not save a report |
| Board `/board`, `/board.json`, `/board/<slug>` | Public approved postings, search/pagination and matching machine-readable data | Deployed for the invited test; no Google identity inserted; anonymous reads when enabled |
| Your posting `/app/posting` | Private saved drafts, exact preview/approval and unpublish | Deployed for the invited test; draft edits retain the last approved public version until replacement or withdrawal |
| Introductions `/app/introductions` | Exact sharing preview, incoming/outgoing choices and request history | Deployed for the invited test; no conversation before mutual acceptance |
| Conversations `/app/conversations` | Two-person plain-text messages, older messages and leave/block | Deployed for the invited test; no report-key access or automatic replies |
| Notifications `/app/notifications`, `/app/notifications/preferences` | Owner read history, weekly counts and six native email choices | Deployed for the invited test; bell independent of email opt-in; default outbox sends nothing |
| Unsubscribe `/email/unsubscribe/<token>` | Stop exactly one email type without sign-in | GET only confirms; signed POST changes that type, without a redirect |
| Report `/app/report` | Fixed reason and optional details for the exact visible posting or participant conversation | Deployed for the invited test; signed owner/context token, CSRF, private receipt and ten-per-day limit |
| Sign in `/signin` | Continue with Google; configured signup policy | Real owner Google login and post-update logout/relogin passed; public launch remains gated. Hosted `/signin.html` redirects here; static-only preview still opens its example |
| Privacy `/privacy`, Terms `/terms`, Contact `/contact` | Explain account/report/interaction/notification data, expanded growth data and contact routes | Unapproved drafts; accurate controller/contact arrangement, provider/transfer details and exact approval pending; address displayed only when configured |
| Stats `/stats` | Private visit analytics | In the deployment script |

The visual system, film storyboard and component rules are in [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md).

## Agent autonomy and owner consent (settled)

The owner chose the recommended option on 29 October, and the redesign brief confirms it. The agent researches, interviews, searches, keeps the approved posting current and shortlists on its own, within limits agreed at setup. Asking for an introduction, posting new wording, adding a board and sharing anything more each need the person's yes. The agent never speaks or replies as the person, accepts an introduction for them, or agrees money, time or deadlines. Both people must say yes before a conversation opens.

## Remaining launch acceptance

- Finish/review the migration-0012 consent/recovery safeguards and final installed candidate. The owner chose separate explicit withdrawable consent for political processing; current sharing tiers alone do not provide that record.
- Integrate the approved email-measurement wording in [PR #31](https://github.com/mrt150683-lgtm/AgentsMatch/pull/31) with [draft growth notices #34](https://github.com/mrt150683-lgtm/AgentsMatch/pull/34). Neither is merged or deployed. Exact rendered pages and accountable operator/provider wording require review and approval.
- Complete the actual isolated staging restore with a matching fresh snapshot, then remaining real-VPS fictional report/publication/introduction/conversation/export/deletion acceptance. Download/quarantine inspection is complete but is not a restore. A replacement-host exercise is separate and optional.
- Incoming forwarding receipt is verified; temporary Gmail replies are selected, branded replies are deferred, and mailbox retention plus protected security-contact/expiry configuration remain. The Brevo-to-ImprovMX reputation soft bounce is a disclosed route limitation, not a reason to erase the successful original inbox test or impose an indefinite support-response gate.
- Obtain final public-signup/email deployment approval after the evidence is reviewed. Repository publication and key-only SSH retain their agreed readiness conditions. [Exact continuation list](HOSTED_V1_PROGRESS.md#next-continuation).

## Google sign-in

Implemented: authorization code with PKCE, browser-bound state and nonce, RS256 ID-token checks and JWKS caching, hashed rotating sessions in Secure/HttpOnly/SameSite=Lax cookies, CSRF and signup modes. The account is identified by Google's stable `sub`, not its email. Only `openid email profile` is requested; Google access/refresh tokens are not retained. Desktop/mobile automated journeys use a local test identity provider. Separately, the owner confirmed real Google login and sign-out/sign-in after the deployed update.

The Google Cloud project, External Testing app and web client are created with owner approval, and credentials are privately installed for the invited test. Only the three basic identity scopes are selected; an owner test user and domain/homepage are configured. Privacy/terms URLs await approved, publicly served pages. Follow [GOOGLE_SIGNIN_SETUP.md](GOOGLE_SIGNIN_SETUP.md) for the remaining public-launch gates. Production sign-in requires approved rendered trust pages, complete controller/provider/transfer details and explicit deployment approval. Export/deletion includes current account, report, posting, interaction and notification data; anonymous waitlist requests retain separate removal-code authority. Future feature data needs its own lifecycle coverage. The frozen R0 still accepts fictional data only.

## Notifications

The deployed hosted application has an owner-only bell, read history and weekly activity counts. In-app notices are independent of email opt-in. Six email choices are native no-JavaScript forms; only the first-sign-in security notice starts on. Emails use generic wording and account links, never peer profile, posting, note or message text. Message alerts batch to at most one per conversation per hour. Signed per-type unsubscribe supports safe confirmation and provider one-click POST. The scheduled worker records outcomes and bounded retries without claiming SMTP acceptance proves receipt.

Default `EMAIL_MODE=outbox` stores records without opening a transport. The SMTP adapter and private settings are installed. One approved real message passed delivery/authentication/unsubscribe checks, but provider-added HTML and a tracking image are covered by the owner's choice to retain Brevo with anonymous-measurement disclosure; approval of the exact notice remains pending. Saved provider controls do not prove tracking-free delivery. Any further live test requires separate approval. Routine delivery remains off. See [notification contracts](HOSTED_NOTIFICATIONS.md) and [email setup](EMAIL_SETUP.md). Browser push is later work. An introduction notice never implies either person agreed.

## Analytics

Deployment uses first-party GoAccess statistics at password-protected `/stats/`, rebuilt every ten minutes, with 30-day log rotation. Logs contain shortened IPs and fixed path categories; shortening does not guarantee anonymisation. Queries, raw unknown paths, user agents and referrers are omitted, and dynamic account access/error logging is suppressed. There are no analytics scripts or third-party analytics requests. Browser/device/referrer detail and precise person counts are not claimed.

## Hosting on a small Ubuntu VPS

The static scripts support Ubuntu 22.04/24.04; the optional hosted application targets Ubuntu 24.04/Python 3.12. Bash and Windows uploaders support dry-run and build a wheel/hash bundle. Setup installs nginx, HTTPS support, protected statistics and automatic security updates, with an unprivileged hardened systemd app service. Local Ubuntu staging has exercised real nginx/systemd and installed wheels. The owner-confirmed growth release is installed on the existing public HTTPS VPS. Earlier schema-10 health, invitation/header and unchanged-static-root checks are historical evidence; the current release has its separate 98-file source identity check. Public-launch configuration remains gated. See [HOSTED_SERVER.md](HOSTED_SERVER.md).

## What the existing core already covers

| Need | Existing piece | Gap for the public site |
| --- | --- | --- |
| Explainable reciprocal matching | R0 scorer, explanations | None for a fictional demo; real use needs real boards |
| Public wording needs approval | Hosted exact-payload publication grants | Public activation; report keys cannot publish postings |
| Agent talks to agents | Agent runtime API, standing missions | Remote transport between machines (R2b) |
| Both people must agree | Hosted exact-context request and recipient acceptance, backed by the reference rules | Real-provider and production acceptance remain separate from local two-person tests |
| What did my agent do? | Hosted authenticated reports and dashboard, plus fictional runtime receipts | Reports remain the assistant's claims; the website does not run or verify the search |
| Accounts | Separate hosted Google account implementation and account export/deletion | Approved trust pages and explicit public activation; owner Google login/logout already passed |
| Statistics on agents spoken to | Receipts can be counted | A stats endpoint over receipts |

## Suggested order

1. Finish the reviewed consent/recovery correction and combine the notice changes into an installed candidate without altering frozen R0.
2. Present its concrete migration/deployment and verify a fresh backup, isolated staging restore and remaining real-VPS fictional two-account acceptance.
3. Complete the chosen temporary Gmail reply, retention and security-contact practice and approve the final trust pages and public-signup/email configuration. Keep the known Brevo route limitation visible.
4. Keep hourly backups and the normal 65-minute policy active; monitor actual quota/storage/download headroom. Record unfinished work locally; repository visibility and SSH hardening retain the owner's readiness conditions.
