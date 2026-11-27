# Optional recommendations — NOT implemented

These are improvements I noticed while building, outside the approved scope.
None of them has been implemented. Each one needs your explicit approval first.

| # | Recommendation | Why it could help | Cost / impact |
|---|---|---|---|
| 1 | QR code on the admin two-factor setup screen | Faster than typing the key into an authenticator app | Adds a small dependency |
| 2 | Move from SQLite to managed PostgreSQL if you need several backend instances | SQLite allows only one writer process; horizontal scaling needs Postgres | Architecture change; free Postgres tiers are limited or expire (Render free Postgres expires after 30 days) |
| 3 | WebAuthn/passkeys for admin sign-in | Stronger than TOTP against phishing | Moderate work; no cost |
| 4 | Email digests of newly qualifying jobs | Users learn about matches without opening the app | Needs an email provider; small cost at volume |
| 5 | Server-side PDF malware scanning of uploads | Extra defence for user-uploaded files | Needs ClamAV or a paid API |
| 6 | Status page for uptime history | Transparency for users during incidents | UptimeRobot offers a free public status page |
| 7 | Annual-plan auto-renewal reminders | Useful once payments exist | Depends on payments being enabled |
| 8 | Import a LinkedIn profile PDF export as a resume source | Faster onboarding | Parser work only |
