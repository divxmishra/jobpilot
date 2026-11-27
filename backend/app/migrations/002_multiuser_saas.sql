-- Multi-user SaaS: accounts, roles, sessions, plans, trials, usage, payments (disabled),
-- activity analytics, admin audit, announcements, support, backups, career roadmap.
-- Existing single-user rows keep user_id NULL until the owner account claims them.

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT,
    password_hash TEXT,                       -- scrypt; NULL for Google-only accounts
    google_sub TEXT UNIQUE,
    email_verified INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL DEFAULT 'user',        -- owner | admin | support | user (server-side only)
    status TEXT NOT NULL DEFAULT 'active',    -- active | suspended | deleted
    totp_secret TEXT,                         -- admin second factor (base32), NULL if not enrolled
    totp_enabled INTEGER NOT NULL DEFAULT 0,
    analytics_opt_out INTEGER NOT NULL DEFAULT 0,
    profile_completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_created ON users(created_at);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,                      -- sha256 of the cookie token
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    elevated_until TEXT,                      -- admin second-factor verified until
    user_agent TEXT,
    ip_hash TEXT,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS auth_tokens (       -- email verification / password reset (hashed, single use)
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL,                    -- verify_email | reset_password | oauth_state
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,                        -- hashed ip / email
    success INTEGER NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts(key, at);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);

-- user scoping of existing tables
ALTER TABLE uploads ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE profile_versions ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE companies ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE jobs ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE jobs ADD COLUMN viewed_at TEXT;
ALTER TABLE resumes ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE applications ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE applications ADD COLUMN outcome_source TEXT;        -- user_reported | verified
ALTER TABLE contacts ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE outreach_drafts ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE audit_events ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE ai_usage ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE discovery_runs ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE tasks ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE notifications ADD COLUMN user_id INTEGER REFERENCES users(id);
CREATE INDEX IF NOT EXISTS idx_uploads_user ON uploads(user_id);
CREATE INDEX IF NOT EXISTS idx_profile_user ON profile_versions(user_id);
CREATE INDEX IF NOT EXISTS idx_companies_user ON companies(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id, status);
CREATE INDEX IF NOT EXISTS idx_resumes_user ON resumes(user_id);
CREATE INDEX IF NOT EXISTS idx_apps_user ON applications(user_id);
CREATE INDEX IF NOT EXISTS idx_contacts_user ON contacts(user_id);
CREATE INDEX IF NOT EXISTS idx_outreach_user ON outreach_drafts(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_events(user_id, at);
CREATE INDEX IF NOT EXISTS idx_ai_usage_user ON ai_usage(user_id, at);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);

-- plans & subscriptions (prices in INR; payment processing disabled)
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    monthly_price REAL,                       -- NULL = price not set yet (shown as "to be announced")
    annual_price REAL,
    annual_enabled INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'INR',
    discount_pct REAL NOT NULL DEFAULT 0,
    promo_label TEXT,
    features TEXT NOT NULL DEFAULT '[]',      -- marketing bullet list
    entitlements TEXT NOT NULL DEFAULT '{}',  -- limits & feature flags (see entitlements.py)
    trial_eligible INTEGER NOT NULL DEFAULT 0,
    is_free_default INTEGER NOT NULL DEFAULT 0,
    available INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id INTEGER NOT NULL REFERENCES plans(id),
    status TEXT NOT NULL,                     -- trialing | active | lifetime | suspended | revoked | cancelled | expired
    source TEXT NOT NULL,                     -- trial | admin_grant | free_grant | lifetime | payment | manual_payment
    billing_period TEXT,                      -- monthly | annual | NULL
    locked_price REAL,                        -- contractual price at subscription time
    currency TEXT NOT NULL DEFAULT 'INR',
    coupon_code TEXT,
    started_at TEXT NOT NULL,
    ends_at TEXT,                             -- NULL = no end (lifetime / until revoked)
    cancelled_at TEXT,
    notes TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subs_user ON subscriptions(user_id, status);

CREATE TABLE IF NOT EXISTS trials (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    plan_id INTEGER REFERENCES plans(id),
    started_at TEXT,
    ends_at TEXT,
    status TEXT NOT NULL,                     -- eligible | active | expired | revoked
    extended_days INTEGER NOT NULL DEFAULT 0,
    reminder_sent_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,                      -- JSON value (number, null = unlimited, bool)
    reason TEXT,
    expires_at TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    UNIQUE (user_id, key)
);

CREATE TABLE IF NOT EXISTS usage_counters (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    period TEXT NOT NULL,                     -- d:YYYY-MM-DD | m:YYYY-MM
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, key, period)
);

CREATE TABLE IF NOT EXISTS coupons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT,
    discount_pct REAL NOT NULL,
    plan_codes TEXT NOT NULL DEFAULT '[]',    -- empty = all paid plans
    max_redemptions INTEGER,
    redeemed_count INTEGER NOT NULL DEFAULT 0,
    valid_from TEXT,
    valid_until TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,                       -- charge | refund
    status TEXT NOT NULL,                     -- captured | failed | refunded | pending
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    gateway TEXT,                             -- razorpay | cashfree | manual
    gateway_txn_id TEXT,
    method TEXT,                              -- upi | card | netbanking | wallet | other
    is_manual INTEGER NOT NULL DEFAULT 0,
    verified INTEGER NOT NULL DEFAULT 0,      -- verified webhook or admin-attested manual record
    recorded_by INTEGER REFERENCES users(id),
    notes TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_at ON payments(created_at);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT NOT NULL UNIQUE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    payment_id INTEGER REFERENCES payments(id) ON DELETE SET NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    issued_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'issued'
);

-- privacy-conscious product analytics (no resumes, answers, secrets or keystrokes)
CREATE TABLE IF NOT EXISTS activity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    session_id TEXT,
    event TEXT NOT NULL,
    feature TEXT,
    actor TEXT NOT NULL DEFAULT 'user',       -- user | system (JobPilot automatic)
    success INTEGER NOT NULL DEFAULT 1,
    error_category TEXT,
    metadata TEXT,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_events(user_id, at);
CREATE INDEX IF NOT EXISTS idx_activity_event ON activity_events(event, at);

CREATE TABLE IF NOT EXISTS admin_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    target_user_id INTEGER,
    details TEXT,
    ip_hash TEXT,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_at ON admin_audit(at);

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT,
    level TEXT NOT NULL DEFAULT 'info',       -- info | warning | success
    audience TEXT NOT NULL DEFAULT 'all',     -- all | users | public
    active INTEGER NOT NULL DEFAULT 1,
    starts_at TEXT,
    ends_at TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    email TEXT,
    subject TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',      -- open | answered | closed
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS support_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
    author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    from_staff INTEGER NOT NULL DEFAULT 0,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    trigger TEXT NOT NULL,                    -- scheduled | change | manual | startup
    status TEXT NOT NULL,                     -- running | succeeded | failed
    backend TEXT,
    object_key TEXT,
    size_bytes INTEGER,
    sha256 TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS skill_progress (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    skill TEXT NOT NULL,
    status TEXT NOT NULL,                     -- not_started | learning | practiced | proficient
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, skill)
);
CREATE TABLE IF NOT EXISTS roadmap_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    jobs_analyzed INTEGER NOT NULL,
    data TEXT NOT NULL                        -- top skills with counts (no personal data)
);
CREATE INDEX IF NOT EXISTS idx_roadmap_user ON roadmap_snapshots(user_id, at);
