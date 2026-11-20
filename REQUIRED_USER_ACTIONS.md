# Required user actions

Everything JobPilot needs from you, why, and how to provide it **without pasting secrets into chat**.
Secrets go only into your local `.env` file or the hosting provider's secret settings.

| # | Dependency | Purpose | Mandatory? | Variable / permission | Status | Blocked feature if missing |
|---|---|---|---|---|---|---|
| 1 | Python 3.11+ (Windows) | Run JobPilot locally | Yes (local) | — | Installed (3.13) | Local app |
| 2 | Access password | Protects the hosted app (your resume and data) | Yes (hosted) | `JOBPILOT_PASSWORD` | Generated on the server during deploy | Hosted app refuses all requests |
| 3 | Session secret | Signs login cookies | Yes (hosted) | `JOBPILOT_SECRET_KEY` | Generated on the server during deploy | Sessions reset on every restart |
| 4 | GitHub repository access | Push the code to `divxmishra/jobpilot` | Yes | Git Credential Manager browser sign-in (no token in chat) | See final status | Code hosting, CI, auto-deploy |
| 5 | AWS account + EC2 instance | Primary hosting (Docker + HTTPS) | For EC2 hosting | SSH key file (`.pem`) kept on your PC; Security Group ports 22/80/443 | See final status | EC2 deployment |
| 6 | GitHub secrets `EC2_HOST`, `EC2_SSH_KEY` | Auto-deploy to EC2 on every push to `main` | Optional | Repository → Settings → Secrets → Actions | Not set | Automatic redeploys (manual `deploy/update.sh` still works) |
| 7 | Render account | Fallback hosting | Only if EC2 isn't used | Render ↔ GitHub authorisation in the Render dashboard | Not needed unless EC2 fails | Render deployment |
| 8 | Gemini or Groq API key (free tier) | Optional AI refinement of job requirements | No | `AI_PROVIDER` + `GEMINI_API_KEY` or `GROQ_API_KEY` | Not configured (deterministic mode) | Nothing essential |
| 9 | Playwright Chromium | Prefill application forms locally | No | `scripts\setup.ps1` installs it | Installed locally | Browser prefill (manual workflow still works) |
| 10 | Target companies | Daily discovery sources | Recommended | Settings → Sources (portals.yml) | Empty | Automatic discovery (manual URLs still work) |

## How to add an optional AI key (free tiers)

- **Gemini:** create a key at https://aistudio.google.com/apikey (free tier with rate limits; usage of free-tier data may be used by Google to improve products — read their terms).
- **Groq:** create a key at https://console.groq.com/keys (free tier with rate limits).

Then, locally, edit `.env` (never commit it):

```
AI_PROVIDER=gemini
GEMINI_API_KEY=<paste here, in the file only>
```

Restart JobPilot, open **Settings → AI & budget**, press **Test connection**, then enable AI.
Daily/monthly caps (₹, max ₹300/month) and a daily call limit pause AI automatically.

## Costs

- Local use: ₹0.
- AWS EC2: covered by the AWS Free Tier / free credits for eligible new accounts. **Outside the
  free tier**, a t3.micro plus its public IPv4 address costs roughly US$10/month, which exceeds the
  ₹300/month budget — stop or terminate the instance if your free tier ends.
- Render free web service: ₹0, but it sleeps when idle and has no persistent disk.
- AI: ₹0 on free tiers; JobPilot tracks estimated cost if you configure per-token prices.
