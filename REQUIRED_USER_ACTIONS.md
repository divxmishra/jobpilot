# Required user actions

This file lists everything that needs your input, why, how to do it securely, whether it blocks anything, and whether it costs money.
**Never paste secrets into chat.** Set them in Render → `jobpilot` service → **Environment**, or in your local `.env` file.

## 1. Blocking or high priority

| # | Action | Why | How (step by step) | Blocks | Cost |
|---|---|---|---|---|---|
| A | **Create the owner (admin) account** | The admin panel needs an owner. It's never granted automatically. | 1. In Render → Environment, add `OWNER_SETUP_TOKEN` = a long random string. Your existing `JOBPILOT_PASSWORD` also works as this secret. 2. Open `https://<your-site>/setup-owner`. 3. Enter the secret and your email/password. 4. Scan or enter the 2FA key in an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password). 5. Open `/admin`. | The admin panel | ₹0 |
| B | **Decide on persistent storage** | Render's free plan has **no persistent disk**. A restart or redeploy wipes the database, and only the last backup can be restored. Real users need persistent storage. | Options: (1) **Render Starter + 1 GB disk**: about US$7 + US$0.25 per month (≈ ₹640), above your ₹300 budget. (2) **Stay on the free plan + restore from cloud backups on boot**: ₹0, but changes since the last backup can be lost. (3) **Self-host on EC2** (`docker-compose.yml`) if SSH access is fixed: free-tier dependent. Then set `JOBPILOT_PERSISTENT_STORAGE=1` only once the disk really exists. | Opening registrations to real users. They stay **closed automatically** in production until you decide (Admin → Settings can override this). | Option 1 costs money and **needs your approval** |
| C | **Backup storage credentials** | Cloud backups protect data from restarts and deploy failures. | Cloudflare R2 (recommended): Dashboard → R2 → create a **private** bucket `jobpilot-backups` → Manage API tokens → create a token with *Object Read & Write* on that bucket only → in Render set `BACKUP_S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com`, `BACKUP_S3_BUCKET`, `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY`, `BACKUP_S3_REGION=auto`, and `BACKUP_ENCRYPTION_KEY` (a long random string; **keep an offline copy**). Then Admin → Settings → Run backup now. | Cloud backups (the local fallback is lost on restart) | R2: 10 GB-month free; Cloudflare may ask for a payment method; usage above the free tier is billed |

## 2. Optional integrations

| # | Action | Why | How | Blocks | Cost |
|---|---|---|---|---|---|
| D | Google sign-in | "Continue with Google" button | Google Cloud Console → APIs & Services → Credentials → OAuth client ID (Web). Authorised redirect URI: `https://<your-site>/api/auth/google/callback`. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in Render. | Google login (email/password works without it) | ₹0 |
| E | Email (SMTP) | Verification emails, password-reset emails, trial reminders | Use a transactional provider with a free tier (e.g. Brevo, about 300 emails/day free) or Gmail with an app password. Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`. | Self-service password reset. Until then, admins can generate a single-use reset link. | Free tiers available |
| F | Set plan prices and confirm limits | Basic and Pro launch as "Pricing to be announced". Default limits are a proposal. | Admin → Plans & Pricing, and Admin → Feature Limits | Paid-plan pricing display | ₹0 |
| G | Review the privacy and terms pages | They're plain-language templates | Review `/privacy` and `/terms` with legal advice before public launch | Public launch | — |
| H | Payment gateway (Razorpay or Cashfree) | Accept payments | **Disabled by design.** When you're ready, ask me to integrate it. You'll create the account, I'll guide you through webhook setup, and activation only happens with your explicit approval. | Online payments | Gateway fees |
| I | AI key (Gemini or Groq free tier) | Optional refinement of job-requirement extraction | Set `AI_PROVIDER` plus the key in Render or `.env`. | Nothing essential | Free tiers |
| J | UptimeRobot monitor URL | Keeps the free service warm; alerts you | Change the existing monitor's URL to `/health`. It's currently 5 min on `/api/health`, which still works. | — | ₹0 |
| K | Close EC2 SSH rule / stop instance | Security and cost hygiene from the earlier EC2 attempt | AWS Console → the instance's security group → remove 22 from `0.0.0.0/0`; stop the instance if unused | — | Avoids charges |

## 3. Already done
- GitHub repository: code pushed to `main`, with CI on every push.
- Render Blueprint `jobpilot`: auto-deploys from `main`. The service is at https://jobpilot-xg5p.onrender.com.
- UptimeRobot monitor: active.
