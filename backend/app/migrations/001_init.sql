-- JobPilot initial schema. SQLite is the authoritative store for live state.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                 -- master_resume | supporting
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    mime TEXT,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    extracted_text TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,               -- draft | verified | superseded
    data TEXT NOT NULL,                 -- JSON profile document
    upload_id INTEGER REFERENCES uploads(id),
    parent_id INTEGER REFERENCES profile_versions(id),
    note TEXT,
    created_at TEXT NOT NULL,
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ats TEXT NOT NULL,                  -- greenhouse | lever | ashby | remotive | career_page
    token TEXT,
    careers_url TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL,
    company TEXT,
    title TEXT,
    location TEXT,
    work_mode TEXT,                     -- remote | hybrid | onsite | unknown
    employment_type TEXT,
    seniority TEXT,
    salary_text TEXT,
    salary_min REAL,
    salary_max REAL,
    salary_currency TEXT,
    posted_at TEXT,
    source TEXT NOT NULL,               -- manual | greenhouse | lever | ashby | remotive | career_page
    source_url TEXT NOT NULL,
    apply_url TEXT,
    alt_urls TEXT NOT NULL DEFAULT '[]',
    external_id TEXT,
    description TEXT,
    extracted_at TEXT,
    verified_at TEXT,
    page_status TEXT NOT NULL DEFAULT 'unknown',  -- active | closed | expired | not_found | inaccessible | unknown
    page_status_detail TEXT,
    extraction_complete INTEGER NOT NULL DEFAULT 0,
    missing_fields TEXT NOT NULL DEFAULT '[]',
    requirements TEXT,                  -- JSON parsed requirements
    eligibility TEXT,                   -- JSON gate results
    eligibility_result TEXT,            -- pass | fail | needs_verification
    match TEXT,                         -- JSON score breakdown
    match_score REAL,
    status TEXT NOT NULL DEFAULT 'new',
    decision TEXT,                      -- shortlisted | dismissed | NULL
    decision_reason TEXT,
    profile_version_id INTEGER,
    duplicate_of TEXT,
    user_confirmations TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON jobs(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS resumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    profile_version_id INTEGER NOT NULL REFERENCES profile_versions(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL,               -- draft | approved | superseded
    content TEXT NOT NULL,              -- JSON tailored resume
    keyword_map TEXT,
    ats TEXT,
    ats_score REAL,
    consistency TEXT,
    pdf_path TEXT,
    docx_path TEXT,
    page_count INTEGER,
    edited INTEGER NOT NULL DEFAULT 0,
    parent_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    resume_id INTEGER REFERENCES resumes(id),
    status TEXT NOT NULL,               -- prepared | applied | interview | offer | rejected | withdrawn
    reviewed INTEGER NOT NULL DEFAULT 0,
    next_action TEXT,
    next_action_date TEXT,
    notes TEXT,
    answers TEXT,                       -- JSON draft answers
    prefill_log TEXT,                   -- JSON of what was prefilled
    rejection_reason TEXT,
    submitted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS application_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    note TEXT,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
    company TEXT,
    name TEXT NOT NULL,
    role TEXT,
    email TEXT,
    profile_url TEXT,
    source_url TEXT,
    source_label TEXT,
    verification TEXT NOT NULL,         -- verified | public_unverified | inferred
    relationship TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outreach_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,                 -- referral | recruiter_email | connection | follow_up
    subject TEXT,
    body TEXT NOT NULL,
    status TEXT NOT NULL,               -- draft | approved | sent_by_user
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    entity_type TEXT,
    entity_id TEXT,
    message TEXT NOT NULL,
    details TEXT,
    source_ref TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_events(at);

CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    purpose TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_inr REAL NOT NULL DEFAULT 0,
    cached INTEGER NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ai_cache (
    key TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovery_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    stats TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,               -- queued | running | succeeded | failed | cancelled
    progress REAL NOT NULL DEFAULT 0,
    message TEXT,
    result TEXT,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    level TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    link TEXT,
    read INTEGER NOT NULL DEFAULT 0
);
