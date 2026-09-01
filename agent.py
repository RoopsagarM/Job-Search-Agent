"""
Core logic for the Job Search Agent — Step 1.

No paid AI model is used here: skill/experience extraction is done with a
free, local keyword + regex approach (fast, no API cost, no rate limits).

Job data comes from JSearch (via RapidAPI), which reads Google for Jobs'
public index — surfacing listings originally posted on LinkedIn, Indeed,
Glassdoor, ZipRecruiter, Monster, and more. This is not scraping those
sites directly (their Terms of Service prohibit that) — it's structured
access to data Google already indexes publicly, and each result reports
which original site it came from.
"""

import io
import re
import time
import requests

SKILL_DICTIONARY = [
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "rust",
    "ruby", "php", "swift", "kotlin", "scala", "r", "sql", "nosql", "mongodb",
    "postgresql", "mysql", "redis", "aws", "azure", "gcp", "docker",
    "kubernetes", "terraform", "react", "angular", "vue", "node.js",
    "django", "flask", "spring", "tensorflow", "pytorch", "scikit-learn",
    "keras", "machine learning", "deep learning", "nlp", "computer vision",
    "llm", "generative ai", "data analysis", "data science", "etl",
    "airflow", "spark", "hadoop", "tableau", "power bi", "excel", "git",
    "ci/cd", "microservices", "rest api", "graphql", "agile", "scrum",
    "product management", "project management", "linux", "bash", "html",
    "css", "figma", "sales", "marketing", "seo", "salesforce", "hubspot",
    "accounting", "finance", "recruiting", "hr",
]

JSEARCH_HOST = "jsearch.p.rapidapi.com"


# ------------------------------------------------------------------ CV parsing
def extract_text_from_upload(uploaded_file) -> str:
    """Extract plain text from a Streamlit UploadedFile (.txt, .pdf, .docx)."""
    name = uploaded_file.name.lower()
    data = uploaded_file.read()

    if name.endswith(".txt"):
        return data.decode("utf-8", errors="ignore")

    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if name.endswith(".docx"):
        import docx
        doc = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)

    raise ValueError(f"Unsupported file type: {name}. Use .txt, .pdf, or .docx")


def extract_years_experience(text: str):
    """
    Looks specifically for phrases like "5 years of experience" rather than
    any bare "N years" anywhere in the text — the old looser pattern could
    pick up an unrelated number (a degree duration, a project timeline,
    etc.) and report it as your total experience instead.
    """
    patterns = [
        r"(\d+)\+?\s*years?\s*(?:of\s*)?(?:professional\s*|relevant\s*|total\s*)?experience",
        r"experience\s*[:\-]?\s*(\d+)\+?\s*years?",
    ]
    years = []
    for pattern in patterns:
        years.extend(int(m) for m in re.findall(pattern, text, flags=re.IGNORECASE))
    return max(years) if years else None


SENIOR_TITLE_MARKERS = [
    "senior", "sr.", "sr ", "lead", "principal", "staff", "director",
    "vp ", "vice president", "head of", "chief", "manager",
]


def seniority_mismatch_penalty(job_title: str, job_description: str, candidate_years) -> int:
    """
    Returns a penalty (0 or negative) to subtract from a job's score when
    the listing clearly targets someone far more senior than the candidate.
    Used by the free keyword scorer; the AI scorer gets the same signal
    via an explicit instruction instead.
    """
    if candidate_years is None:
        return 0

    title_lower = job_title.lower()
    text = f"{job_title} {job_description}".lower()

    penalty = 0
    if candidate_years <= 2 and any(marker in title_lower for marker in SENIOR_TITLE_MARKERS):
        penalty -= 5

    required = re.findall(r"(\d+)\+?\s*years?\s*(?:of\s*)?(?:professional\s*|relevant\s*)?experience", text)
    if required:
        max_required = max(int(r) for r in required)
        if max_required > candidate_years + 2:
            penalty -= 4

    return penalty


def extract_skills(text: str):
    lower = text.lower()
    return [s for s in SKILL_DICTIONARY if s in lower]


def score_job(title: str, description: str, terms: list, candidate_years=None):
    text = f"{title} {description}".lower()
    matched = [t for t in terms if t in text]
    score = len(matched) + seniority_mismatch_penalty(title, description, candidate_years)
    return matched, score


# -------------------------------------------------------------- JSearch source
def search_jsearch(title, location, country, api_key, num_pages=1, max_retries=2, date_posted="all"):
    query = f"{title} in {location}" if location else title
    url = f"https://{JSEARCH_HOST}/search"
    headers = {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": JSEARCH_HOST}
    params = {
        "query": query, "page": "1", "num_pages": str(num_pages),
        "country": country, "date_posted": date_posted,
    }

    attempt = 0
    while True:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=45)
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                attempt += 1
                time.sleep(2)
                continue
            raise
        if resp.status_code == 429 and attempt < max_retries:
            attempt += 1
            time.sleep(5)  # brief backoff for the free-tier rate limit, then retry once
            continue
        resp.raise_for_status()
        return resp.json().get("data", [])


def _normalize_jsearch(job: dict) -> dict:
    location_parts = [job.get("job_city"), job.get("job_state"), job.get("job_country")]
    location_name = ", ".join(p for p in location_parts if p) or "Not listed"
    return {
        "id": f"jsearch:{job.get('job_id')}",
        "title": job.get("job_title", ""),
        "company": job.get("employer_name") or "Not listed",
        "location": location_name,
        "remote": "Yes" if job.get("job_is_remote") else "Not stated",
        "salary_min": job.get("job_min_salary") or "",
        "salary_max": job.get("job_max_salary") or "",
        "description": job.get("job_description", "") or "",
        "url": job.get("job_apply_link", "") or "",
        "source": job.get("job_publisher") or "Google for Jobs",
    }


# ------------------------------------------------------------------- Orchestrator
def run_search(cv_text, titles, country, location, api_key,
                results_per_title=20, max_rows=50, anthropic_api_key=None,
                max_suggested_titles=2, request_delay_seconds=1.5, date_posted="all"):
    """
    Returns (jobs, skills, years, errors). jobs is deduped (by title +
    company) and sorted by match score, capped at max_rows. Each job dict
    includes a "source" field naming the original site (LinkedIn, Indeed,
    Glassdoor, ZipRecruiter, etc.) reported by JSearch.

    If anthropic_api_key is provided, an AI layer (Claude via the Anthropic API)
    analyzes the resume for richer skills/seniority/suggested titles and
    judges each job's real fit, instead of plain keyword matching. If the
    key is absent, or the agent call fails for any reason, this silently
    falls back to the free keyword-based approach — the app always works.

    max_suggested_titles caps how many AI-suggested extra titles get
    searched (on top of the ones you typed), and request_delay_seconds
    pauses between JSearch calls — both exist to stay under RapidAPI's
    free-tier rate limit, which is easy to trip when several titles are
    searched back-to-back.
    """
    used_ai = False
    ai_note = None
    suggested_titles = []

    if anthropic_api_key:
        try:
            import llm_agent
            profile = llm_agent.analyze_resume(cv_text, anthropic_api_key, seed_titles=titles)
            skills = profile.get("skills") or extract_skills(cv_text)
            years = profile.get("years_experience")
            suggested_titles = [t for t in profile.get("suggested_titles", []) if t not in titles]
            suggested_titles = suggested_titles[:max_suggested_titles]
            used_ai = True
        except Exception as e:
            ai_note = f"AI resume analysis unavailable, used keyword matching instead ({e})"
            skills = extract_skills(cv_text)
            years = extract_years_experience(cv_text)
    else:
        skills = extract_skills(cv_text)
        years = extract_years_experience(cv_text)

    search_titles = titles + suggested_titles
    seen_keys = set()
    all_jobs = []
    errors = []
    if ai_note:
        errors.append(ai_note)

    for i, title in enumerate(search_titles):
        if i > 0:
            time.sleep(request_delay_seconds)  # avoid bursting the free-tier rate limit

        fallback_terms = skills if skills else title.lower().split()

        try:
            num_pages = max(1, results_per_title // 10)
            raw_jobs = [
                _normalize_jsearch(j)
                for j in search_jsearch(title, location, country, api_key, num_pages, date_posted=date_posted)
            ]
        except requests.RequestException as e:
            errors.append(f"'{title}': {e}")
            continue

        for job in raw_jobs:
            dedup_key = (job["title"].strip().lower(), job["company"].strip().lower())
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            matched, score = score_job(job["title"], job["description"], fallback_terms, candidate_years=years)
            job["matched"] = matched
            job["score"] = score
            job["reasoning"] = ""
            job["search_title"] = title
            all_jobs.append(job)

    # AI relevance scoring — replaces the keyword score if available.
    if used_ai and all_jobs:
        try:
            import llm_agent
            scored = llm_agent.score_jobs(profile, all_jobs[:40], anthropic_api_key)
            for i, job in enumerate(all_jobs[:40]):
                if i in scored:
                    job["score"] = scored[i]["score"]
                    job["reasoning"] = scored[i]["reasoning"]
        except Exception as e:
            errors.append(f"AI job scoring unavailable, used keyword scores instead ({e})")

    all_jobs.sort(key=lambda j: j["score"], reverse=True)
    return all_jobs[:max_rows], skills, years, errors

