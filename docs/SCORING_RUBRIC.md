# JobPilot scoring rubrics

Both rubrics are deterministic and reproducible: the same verified profile and the same
job description always give the same result. Scores are **truncated** to one decimal
(84.99 → 84.9), never rounded up, so a job can't cross a threshold by rounding.

## 1. Hard eligibility gates (evaluated first)

A job is recommended only if **every** gate passes **and** the match score is at or above
the user's threshold (default 85%). A failed gate can never be overridden by a high score.

| Gate | Fails when | Needs verification when |
|---|---|---|
| Posting is open | Closed, expired (validThrough passed), removed (404/410), no longer on the official board | Page inaccessible / manual entry (user may confirm a manual entry is open) |
| Freshness | Stated posting date older than *max posting age* | — (a missing date is never assumed) |
| Mandatory experience | Verified experience < stated mandatory minimum | Undated or year-only positions make the total borderline |
| Mandatory education | Highest verified qualification below requirement (unless "or equivalent experience" and ≥2 verified years) | No verified education entries |
| Work authorisation | Required country not in the user's declared countries | Requirement stated but no countries declared |
| Licences / clearances | — | Required credential not in verified certifications |
| Work mode / location / employment type / seniority / salary | Outside the user's stated preferences | Location or salary period unclear |
| Exclusions | Excluded employer or keyword; fees, unpaid, commission-only, sensitive-data requests (per settings) | Unidentifiable employer; instruction-like text aimed at tools (prompt injection) |

Experience is computed only from verified, dated positions; overlapping periods are merged
and internships are counted separately. "Fresher" vs "experienced" is derived from these
facts — there is no fresher-only logic anywhere.

## 2. Resume-to-JD match (rubric `match-v1`)

| Component | Weight | How it is computed |
|---|---|---|
| Required skills | 35 | Share of the JD's required skills evidenced in verified facts. A *related* skill (e.g. MySQL for PostgreSQL) earns 0.25 credit and is labelled "transferable", never claimed. |
| Preferred skills | 10 | Same, for preferred / nice-to-have skills |
| Responsibilities & keywords | 15 | Share of the JD's 25 most frequent meaningful terms found in verified text |
| Experience | 15 | min(1, verified years ÷ required years) |
| Seniority fit | 10 | Role level vs verified career stage (same = 10, one level apart = 6) |
| Education & certifications | 5 | Requirement met |
| Role / domain relevance | 5 | Overlap of the job title with verified titles, target roles or headline |
| Preference fit | 5 | Work mode, employment type and location satisfied |

Components with no data in the JD (e.g. no experience requirement) are *not applicable* and
their weight is excluded: `score = 100 × Σ points ÷ Σ applicable weights`.

**Reliability rule:** if fewer than 2 concrete skills can be identified, or applicable weight
is below 50, confidence is "low" and the job goes to *Needs Review* — it is never auto-recommended.

JD interpretation splits the posting into required / preferred / responsibilities / other
sections. Optional AI refinement (off by default) can only add requirements whose supporting
quote appears verbatim in the JD.

## 3. ATS compatibility (rubric `ats-v1`)

Computed on the text extracted from the **actually rendered PDF** (and the DOCX for integrity).

| Component | Weight |
|---|---|
| Keyword coverage — required skills ×2, preferred ×1, top JD terms ×0.5 | 40 |
| Standard headings (Summary, Skills, Experience/Projects, Education) | 10 |
| Parsing & file integrity (content recovered from PDF, DOCX readable, name found) | 15 |
| Chronology & date consistency | 10 |
| ATS-safe formatting (single column, no tables/images; ≤ 2 pages) | 10 |
| Bullet relevance to the JD | 10 |
| Contact details | 5 |

Bands: **95–100** target achieved · **90–94** close · **< 90** significant gap.
If 95 can't be reached truthfully, the actual score is shown with the label
**"Below 95 target – truthful version"** plus the limiting gaps and safe improvements.
This is JobPilot's own estimate — not an employer's ATS score or a guarantee.

## 4. Truthfulness check (blocks approval)

Every tailored resume is checked against the verified profile: each skill must be verified
(or evidenced in verified bullets), each bullet must trace to a verified bullet, titles,
employers, dates, education and certifications must match, and user edits may not introduce
new skills, numbers or metrics. Any violation blocks approval until fixed or until the fact
is added to the profile through a versioned update.
