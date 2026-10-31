# Google sign-in setup

Prepared 30 October 2026 against Google's current Google Auth Platform documentation. The owner completes the account and credential steps. This guide does not authorize a production deployment.

Current checkpoint: the owner approved the External Testing app, temporary support contact, API Services User Data Policy and web-client creation. The web client exists with the callback below, no JavaScript origins and only the three basic identity scopes. The domain and homepage are configured, and the owner's account is listed for testing. Credentials were privately installed for the explicitly approved VPS login test; the owner confirmed successful real Google login on 30 October. Sign-out/relogin and wider acceptance remain to be recorded. Privacy/terms URLs remain unset until their exact pages are approved and served. Project identifiers, personal addresses and credentials stay outside tracked documentation. Local test-provider journeys do not replace real-provider evidence. See [current deployment](VPS_LOGIN_TEST.md).

## 1. Create the project

1. Open [Google Cloud Console](https://console.cloud.google.com/) with the Google account that should own the service.
2. Open the project selector, choose **New project**, name it **Connect the Bots**, and create it.
3. Select that project. Open **Google Auth Platform**. If asked to configure the app, choose **Get started**.
4. Use **Connect the Bots** as the app name and choose a support email you monitor. Enter the developer contact email when asked.

Google's sign-in setup uses a Cloud project, consent branding and a web OAuth client. Do not enable Gmail, Drive or other data APIs for this account-only flow. [Google OpenID Connect setup](https://developers.google.com/identity/openid-connect/openid-connect)

## 2. Set the audience and branding

1. Under **Audience**, select **External** so people outside your organisation can sign in. Keep the project in **Testing** while completing staging. Add the Google accounts that will carry out the staging check to **Test users**.
2. Under **Branding**, enter these URLs after the final pages are approved and served:
   - Home: `https://connectthebots.net/`
   - Privacy: `https://connectthebots.net/privacy`
   - Terms: `https://connectthebots.net/terms`
3. Add `connectthebots.net` under **Authorised domains**. Use the domain without a scheme or path.
4. Save. Follow any domain-ownership verification prompts using the account that controls the domain.

External audience and publication status are separate settings. Branding verification may be needed for the app name or logo to appear. Review the console's verification requirements; do not treat successful testing as approval for publication. [Audience settings](https://support.google.com/cloud/answer/15549945), [branding settings](https://support.google.com/cloud/answer/15549049)

Google's Audience guidance exempts requests limited to basic identity scopes (`openid`, email and profile) from Testing's test-user restriction, warning and seven-day authorization expiry. This app requests only those scopes. Do not describe Google Testing status alone as preventing another person's login; the application's own `SIGNUPS` mode independently controls account creation. Review branding and publication requirements separately before launch. [Basic-identity exception](https://support.google.com/cloud/answer/15549945)

## 3. Choose the scopes

Under **Data Access**, choose **Add or remove scopes** and select only:

- `openid`
- `https://www.googleapis.com/auth/userinfo.email`
- `https://www.googleapis.com/auth/userinfo.profile`

The application requests these as `openid email profile`. It uses Google to verify the account and obtain its name and email; it does not request offline access or retain Google access/refresh tokens. [Google's scope definitions](https://developers.google.com/identity/openid-connect/openid-connect)

## 4. Create the web client

1. Open **Clients** and choose **Create client**.
2. Choose **Web application** and name it **Connect the Bots web**.
3. Under **Authorised redirect URIs**, add exactly:

   `https://connectthebots.net/auth/google/callback`

4. If using a separate HTTPS staging host, add its exact `/auth/google/callback` URL as well, or create a separate staging client. The server's `PUBLIC_BASE_URL` must match that host.
5. Leave **Authorised JavaScript origins** empty: this implementation redirects through the server and loads no Google browser script.
6. Choose **Create**. Save the client ID and the client secret securely when shown. Google may only display the secret at creation. Do not paste the secret into chat, source files, screenshots or GitHub.

Redirect URIs must match exactly, including scheme, host and path. Public staging needs HTTPS; Google's localhost exception does not apply to arbitrary IP addresses or hostnames. [Google OAuth client settings](https://support.google.com/cloud/answer/15549257), [web-server credentials](https://developers.google.com/identity/protocols/oauth2/web-server)

## 5. Configure the staged service

Using the private server console, edit `/etc/connectthebots/app.env` as root. Its owner remains root and its mode remains `0600`. Enter the real values only there:

```dotenv
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
ACCOUNT_SIGNIN_ENABLED=true
SIGNUPS=open
EMAIL_MODE=outbox
```

For staging, retain `APP_ENV=staging` and the correct HTTPS `PUBLIC_BASE_URL`. `SESSION_SECRET` is generated by setup; keep it private and stable. Staging emails remain recorded in the outbox and are never delivered.

Restart the staged service, visit `/signin`, and complete Google sign-in. Check that the account page uses the expected Google account, sign out, then confirm the previous session no longer opens `/app`. A localhost fixture test proves the implementation's protocol handling; it does not replace this real Google check.

## 6. Before public sign-in

- Approve the exact privacy, terms and contact pages with an accurate controller identity, contact details, hosting provider and email provider. A correspondence address is optional in configuration and appears only if explicitly supplied; no personal name is populated by default.
- Enter the controller, contact, provider and `LEGAL_TRANSFER_DETAILS` environment fields. Confirm any international transfers, safeguards and how people can obtain the relevant information. Set `LEGAL_PAGES_APPROVED=true` only after the exact rendered pages have been approved.
- Review Google's **Verification centre** and complete any requested brand or app verification. Use **Audience → Publish app** only when ready for public access.
- Complete the remaining release checks, review staging evidence, and give written approval for that specific production deployment.

Account export and deletion include current profile, report, board, interaction and notification data. Shared introduction context is cleared on account deletion, the deleting author's messages become [deleted], and the survivor keeps their own messages in a closed conversation. Notification preferences and the controlled SMTP worker are implemented; actual provider delivery remains unverified. `EMAIL_MODE=outbox` records events without sending them and is not evidence that the full service is ready to launch. Brevo Free is the selected initial provider; see [email setup](EMAIL_SETUP.md).

`ACCOUNT_SIGNIN_ENABLED=false` is the safe default. Production refuses enabled sign-in without legal-page approval. Google configuration alone does not establish real mail delivery, monitored operations or off-server recovery. The current implementation and remaining release evidence are recorded in [the continuation record](HOSTED_V1_PROGRESS.md).

## If sign-in fails

| Symptom | Check |
| --- | --- |
| `redirect_uri_mismatch` at Google | Client redirect URI equals `PUBLIC_BASE_URL` plus `/auth/google/callback`, with no trailing slash. |
| Access restricted during testing | Correct project, External audience, intended test account and current publishing status. |
| “Accounts are not open yet” | Feature flag and both client settings are configured; production legal approval is present. |
| Invalid or expired attempt | Start again from `/signin`; the state is single use, bound to the browser and expires after ten minutes. Cookies require HTTPS. |
| Verification not complete | Follow the requirements shown in Google's console; do not add unrelated scopes as a workaround. |

Google notes that client-setting changes may take time to propagate. Keep real credentials out of diagnostic screenshots and logs. [Google client troubleshooting](https://support.google.com/cloud/answer/15549257)
