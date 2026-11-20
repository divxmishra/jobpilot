"""Skill taxonomy and keyword detection.

The taxonomy maps a canonical skill name to aliases and a category. It covers
software, data, design, product, marketing, sales, finance, operations, HR and
healthcare so the system is not tied to one career type. Skills found in a
candidate's resume that are not in the taxonomy are still kept (as "custom"
skills) and matched literally against job descriptions.

`RELATED` groups define *legitimately transferable* skills: knowing one member
is relevant context for another but is NEVER treated as possessing it.
"""
from __future__ import annotations

import re
from functools import lru_cache

# canonical: (category, [aliases])
TAXONOMY: dict[str, tuple[str, list[str]]] = {
    # --- programming languages
    "Python": ("language", ["python3", "python 3"]),
    "Java": ("language", []),
    "JavaScript": ("language", ["js", "ecmascript", "es6"]),
    "TypeScript": ("language", ["ts"]),
    "C": ("language", ["c language", "c programming"]),
    "C++": ("language", ["cpp", "c plus plus"]),
    "C#": ("language", ["c sharp", "csharp"]),
    "Go": ("language", ["golang"]),
    "Rust": ("language", []),
    "Ruby": ("language", []),
    "PHP": ("language", []),
    "Kotlin": ("language", []),
    "Swift": ("language", []),
    "Scala": ("language", []),
    "R": ("language", ["r programming", "r language"]),
    "MATLAB": ("language", []),
    "Dart": ("language", []),
    "Bash": ("language", ["shell scripting", "shell script", "bash scripting"]),
    "PowerShell": ("language", []),
    "SQL": ("data", ["structured query language"]),
    "HTML": ("frontend", ["html5"]),
    "CSS": ("frontend", ["css3"]),
    # --- frontend
    "React": ("frontend", ["react.js", "reactjs"]),
    "Next.js": ("frontend", ["nextjs", "next js"]),
    "Angular": ("frontend", ["angularjs", "angular.js"]),
    "Vue.js": ("frontend", ["vue", "vuejs"]),
    "Svelte": ("frontend", []),
    "Redux": ("frontend", []),
    "Tailwind CSS": ("frontend", ["tailwind", "tailwindcss"]),
    "Bootstrap": ("frontend", []),
    "jQuery": ("frontend", []),
    "Webpack": ("frontend", []),
    "Vite": ("frontend", []),
    "React Native": ("mobile", []),
    "Flutter": ("mobile", []),
    "Android": ("mobile", ["android development"]),
    "iOS": ("mobile", ["ios development"]),
    # --- backend
    "Node.js": ("backend", ["nodejs", "node js", "node"]),
    "Express": ("backend", ["express.js", "expressjs"]),
    "Django": ("backend", []),
    "Flask": ("backend", []),
    "FastAPI": ("backend", []),
    "Spring Boot": ("backend", ["spring", "spring framework"]),
    "Ruby on Rails": ("backend", ["rails"]),
    "Laravel": ("backend", []),
    ".NET": ("backend", ["dotnet", "asp.net", ".net core"]),
    "GraphQL": ("backend", []),
    "REST APIs": ("backend", ["rest", "restful", "rest api", "restful apis", "restful api"]),
    "gRPC": ("backend", []),
    "Microservices": ("backend", ["microservice", "micro-services"]),
    # --- data stores
    "PostgreSQL": ("database", ["postgres", "postgresql"]),
    "MySQL": ("database", []),
    "SQLite": ("database", []),
    "MongoDB": ("database", ["mongo"]),
    "Redis": ("database", []),
    "Elasticsearch": ("database", ["elastic search", "opensearch"]),
    "Oracle Database": ("database", ["oracle db", "pl/sql", "plsql"]),
    "SQL Server": ("database", ["mssql", "microsoft sql server", "t-sql"]),
    "DynamoDB": ("database", []),
    "Cassandra": ("database", []),
    "Firebase": ("database", ["firestore"]),
    # --- cloud / devops
    "AWS": ("cloud", ["amazon web services"]),
    "Azure": ("cloud", ["microsoft azure"]),
    "Google Cloud": ("cloud", ["gcp", "google cloud platform"]),
    "Docker": ("devops", ["containers", "containerization"]),
    "Kubernetes": ("devops", ["k8s"]),
    "Terraform": ("devops", []),
    "Ansible": ("devops", []),
    "Jenkins": ("devops", []),
    "GitHub Actions": ("devops", []),
    "CI/CD": ("devops", ["ci cd", "continuous integration", "continuous delivery", "continuous deployment"]),
    "Linux": ("devops", ["unix"]),
    "Git": ("tools", ["github", "gitlab", "version control"]),
    "Nginx": ("devops", []),
    "Prometheus": ("devops", []),
    "Grafana": ("devops", []),
    "Networking": ("devops", ["tcp/ip", "computer networks"]),
    "Cybersecurity": ("security", ["information security", "infosec", "cyber security"]),
    "Penetration Testing": ("security", ["pentesting", "pen testing"]),
    # --- data / ML
    "Pandas": ("data", []),
    "NumPy": ("data", ["numpy"]),
    "Scikit-learn": ("data", ["sklearn", "scikit learn"]),
    "TensorFlow": ("ml", []),
    "PyTorch": ("ml", ["torch"]),
    "Keras": ("ml", []),
    "Machine Learning": ("ml", ["ml"]),
    "Deep Learning": ("ml", []),
    "Natural Language Processing": ("ml", ["nlp"]),
    "Computer Vision": ("ml", ["opencv"]),
    "Large Language Models": ("ml", ["llm", "llms", "generative ai", "genai"]),
    "Data Analysis": ("data", ["data analytics", "analytics"]),
    "Data Visualization": ("data", ["data visualisation"]),
    "Statistics": ("data", ["statistical analysis"]),
    "Excel": ("data", ["microsoft excel", "ms excel", "advanced excel", "spreadsheets"]),
    "Power BI": ("data", ["powerbi"]),
    "Tableau": ("data", []),
    "Looker": ("data", []),
    "Apache Spark": ("data", ["spark", "pyspark"]),
    "Hadoop": ("data", []),
    "Airflow": ("data", ["apache airflow"]),
    "ETL": ("data", ["elt", "data pipelines", "data pipeline"]),
    "Data Warehousing": ("data", ["data warehouse", "snowflake", "bigquery", "redshift"]),
    "dbt": ("data", []),
    "Kafka": ("data", ["apache kafka"]),
    # --- testing / practice
    "Unit Testing": ("practice", ["pytest", "jest", "junit", "unit tests"]),
    "Test Automation": ("practice", ["selenium", "cypress", "playwright", "automation testing"]),
    "Manual Testing": ("practice", ["qa testing", "quality assurance"]),
    "Agile": ("practice", ["scrum", "kanban", "agile methodologies"]),
    "Data Structures": ("practice", ["data structures and algorithms", "dsa", "algorithms"]),
    "Object-Oriented Programming": ("practice", ["oop", "oops", "object oriented programming"]),
    "System Design": ("practice", ["distributed systems"]),
    "Jira": ("tools", []),
    "Figma": ("design", []),
    "Adobe Photoshop": ("design", ["photoshop"]),
    "Adobe Illustrator": ("design", ["illustrator"]),
    "UI Design": ("design", ["user interface design", "ui"]),
    "UX Design": ("design", ["user experience", "ux", "ux research", "user research"]),
    "Wireframing": ("design", ["prototyping", "wireframes"]),
    # --- product / business
    "Product Management": ("product", ["product manager", "product roadmap"]),
    "Project Management": ("business", ["pmp", "project planning"]),
    "Stakeholder Management": ("business", []),
    "Business Analysis": ("business", ["business analyst", "requirements gathering"]),
    "Communication": ("soft", ["communication skills", "verbal communication", "written communication"]),
    "Leadership": ("soft", ["team leadership", "people management"]),
    "Problem Solving": ("soft", ["problem-solving"]),
    "Teamwork": ("soft", ["collaboration", "team player"]),
    # --- marketing / sales
    "SEO": ("marketing", ["search engine optimization", "search engine optimisation"]),
    "SEM": ("marketing", ["google ads", "ppc", "paid search"]),
    "Digital Marketing": ("marketing", []),
    "Social Media Marketing": ("marketing", ["social media"]),
    "Content Writing": ("marketing", ["copywriting", "content creation"]),
    "Email Marketing": ("marketing", []),
    "Google Analytics": ("marketing", ["ga4"]),
    "CRM": ("sales", ["customer relationship management"]),
    "Salesforce": ("sales", []),
    "HubSpot": ("sales", []),
    "Lead Generation": ("sales", []),
    "B2B Sales": ("sales", ["b2b"]),
    "Customer Service": ("sales", ["customer support", "customer success"]),
    "Negotiation": ("sales", []),
    # --- finance / accounting
    "Accounting": ("finance", ["bookkeeping"]),
    "Financial Analysis": ("finance", ["financial modeling", "financial modelling"]),
    "Tally": ("finance", ["tally erp", "tally prime"]),
    "GST": ("finance", ["goods and services tax"]),
    "Taxation": ("finance", ["income tax", "tax"]),
    "Auditing": ("finance", ["audit"]),
    "SAP": ("finance", ["sap erp", "sap fico"]),
    "QuickBooks": ("finance", []),
    "Budgeting": ("finance", ["forecasting"]),
    # --- operations / HR / other
    "Recruitment": ("hr", ["talent acquisition", "recruiting", "hiring"]),
    "Payroll": ("hr", []),
    "Supply Chain Management": ("operations", ["supply chain", "logistics"]),
    "Inventory Management": ("operations", []),
    "Operations Management": ("operations", []),
    "Six Sigma": ("operations", ["lean six sigma"]),
    "AutoCAD": ("engineering", []),
    "SolidWorks": ("engineering", []),
    "Embedded Systems": ("engineering", ["embedded c", "microcontrollers", "arduino"]),
    "IoT": ("engineering", ["internet of things"]),
    "Patient Care": ("healthcare", []),
    "Clinical Research": ("healthcare", []),
    "Teaching": ("education", ["tutoring", "lesson planning"]),
}

# Transferable-skill groups: related context only, never proof of the other skill.
RELATED: list[set[str]] = [
    {"PostgreSQL", "MySQL", "SQLite", "SQL Server", "Oracle Database"},
    {"MongoDB", "DynamoDB", "Cassandra", "Firebase"},
    {"React", "Angular", "Vue.js", "Svelte", "Next.js"},
    {"Django", "Flask", "FastAPI"},
    {"Node.js", "Express"},
    {"AWS", "Azure", "Google Cloud"},
    {"TensorFlow", "PyTorch", "Keras"},
    {"Power BI", "Tableau", "Looker"},
    {"Jenkins", "GitHub Actions", "CI/CD"},
    {"Java", "Kotlin", "Scala"},
    {"JavaScript", "TypeScript"},
    {"C", "C++"},
    {"Adobe Photoshop", "Adobe Illustrator", "Figma"},
    {"Salesforce", "HubSpot", "CRM"},
    {"Tally", "QuickBooks", "SAP"},
    {"Unit Testing", "Test Automation"},
]

_CANON_LOWER = {k.lower(): k for k in TAXONOMY}


def _alias_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for canon, (_, aliases) in TAXONOMY.items():
        idx[canon.lower()] = canon
        for a in aliases:
            idx[a.lower()] = canon
    return idx


ALIASES = _alias_index()

_CAPITALIZED_ONLY = {"spark", "spring", "swift", "rust", "excel", "node", "rest", "rails", "keras", "looker",
                     "tableau", "vite", "express", "flask", "go", "unix", "ruby", "dart", "scala", "kotlin"}
_GO_FALSE = re.compile(r"\s+(?:to|ahead|live|beyond|through|out|back|above|deeper)\b", re.IGNORECASE)


def canonical(name: str) -> str | None:
    return ALIASES.get(name.strip().lower())


def category(name: str) -> str:
    c = canonical(name)
    return TAXONOMY[c][0] if c else "custom"


def related_to(skill: str) -> set[str]:
    out: set[str] = set()
    for g in RELATED:
        if skill in g:
            out |= g
    out.discard(skill)
    return out


@lru_cache(maxsize=4096)
def _compiled(term: str, case_sensitive: bool) -> re.Pattern:
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r"(?<![A-Za-z0-9+#])" + re.escape(term) + r"(?![A-Za-z0-9+#]|\.[A-Za-z0-9])", flags)


_SINGLE_LETTER = {
    t: re.compile(r"(?:(?<=[,(/|•·:;] )|(?<=[,(/|•·:;])|(?<=\band )|(?<=\bin )|(?<=\bof )|(?<=\bwith )|^)" + t +
                  r"(?=\s*(?:[,)/|•·;]|\band\b|\bprogramming\b|\blanguage\b|$))", re.MULTILINE)
    for t in ("C", "R")
}


def _term_hits(text: str, alias: str, canon: str) -> list[re.Match]:
    """Find occurrences of one alias, guarding against common-English false positives."""
    if canon in _SINGLE_LETTER and alias == canon.lower():
        return list(_SINGLE_LETTER[canon].finditer(text))
    if alias == "go":
        return [m for m in _compiled("Go", True).finditer(text) if not _GO_FALSE.match(text, m.end())]
    if len(alias) <= 4 and alias.replace("/", "").isalpha():
        # Short acronyms (AWS, UI, SEO, Jira, dbt) must match as written in upper/canonical case.
        hits = list(_compiled(alias.upper(), True).finditer(text))
        if canon.lower() == alias and canon != alias.upper():
            hits += list(_compiled(canon, True).finditer(text))
        return hits
    if alias in _CAPITALIZED_ONLY:
        return [m for m in _compiled(alias, False).finditer(text) if m.group(0)[0].isupper()]
    return list(_compiled(alias, False).finditer(text))


def find_skills(text: str, extra_terms: list[str] | None = None) -> dict[str, list[str]]:
    """Return {canonical_skill: [matched surface forms]} found in text.

    `extra_terms` are candidate custom skills (outside the taxonomy) matched literally.
    """
    found: dict[str, list[str]] = {}
    if not text:
        return found
    for alias, canon in ALIASES.items():
        hits = _term_hits(text, alias, canon)
        if hits:
            forms = found.setdefault(canon, [])
            for h in hits[:3]:
                s = h.group(0).strip()
                if s and s not in forms:
                    forms.append(s)
            if not forms:
                found.pop(canon, None)
    for term in extra_terms or []:
        t = term.strip()
        if len(t) < 2 or canonical(t):
            continue
        if _compiled(t, False).search(text):
            found.setdefault(t, [t])
    return found


def normalize_skill_list(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        name = it.strip().strip(".;:-•*").strip()
        if not name or len(name) > 60:
            continue
        canon = canonical(name) or name
        if canon.lower() not in seen:
            seen.add(canon.lower())
            out.append(canon)
    return out
