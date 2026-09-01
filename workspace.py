"""
File organization using a real folder path you choose (e.g. a folder on
your Desktop) — not a name mapped to a hidden location inside the project.

<your-chosen-folder>/
  main_matches.xlsx     — ONLY the jobs you've explicitly chosen to keep.
                           Search results you don't check are never
                           written here at all.
  resumes/<Company>/    — approved tailored resumes, one folder per company
"""

import re
from pathlib import Path

import pandas as pd

MAIN_SHEET_COLUMNS = [
    "Title", "Company", "Location", "Remote", "Salary Min",
    "Salary Max", "Source", "Score", "Skills Matched", "AI Reasoning",
    "Description", "Apply Link", "Search Titles", "Found At", "Resume Status",
]


def safe_folder_name(name: str) -> str:
    """Sanitizes a single folder/file name component (e.g. a company name) — not a full path."""
    name = re.sub(r"[^\w\-. ]", "_", name).strip()
    return name or "default"


def workspace_dir(folder_path: str) -> Path:
    """folder_path is a real path you typed, e.g. ~/Desktop/Job — used as-is."""
    d = Path(folder_path).expanduser()
    (d / "resumes").mkdir(parents=True, exist_ok=True)
    return d


def main_excel_path(folder_path: str) -> Path:
    return workspace_dir(folder_path) / "main_matches.xlsx"


def load_main_sheet(folder_path: str) -> pd.DataFrame:
    path = main_excel_path(folder_path)
    if not path.exists():
        return pd.DataFrame(columns=MAIN_SHEET_COLUMNS)
    df = pd.read_excel(path)

    # Excel round-trips of empty text columns get inferred as float64 (all-NaN),
    # which then rejects later string writes. Force text columns back to str.
    text_cols = [
        "Title", "Company", "Location", "Remote", "Source", "Skills Matched",
        "AI Reasoning", "Description", "Apply Link", "Search Titles",
        "Found At", "Resume Status",
    ]
    for col in text_cols:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    return df


def save_main_sheet(folder_path: str, df: pd.DataFrame):
    path = main_excel_path(folder_path)
    df.to_excel(path, index=False)


def save_selected_jobs(folder_path: str, selected_jobs: list, search_titles: list, found_at: str) -> pd.DataFrame:
    """
    Adds ONLY the jobs passed in (already filtered to the ones checked in
    the UI) to the main sheet, deduped by (Title, Company). Anything not
    passed here is never written — this is the single point where a job
    goes from "found" to "saved."
    """
    df = load_main_sheet(folder_path)
    existing_keys = set(zip(df["Title"].astype(str), df["Company"].astype(str))) if not df.empty else set()

    new_rows = []
    for job in selected_jobs:
        key = (str(job["title"]), str(job["company"]))
        if key in existing_keys:
            continue
        existing_keys.add(key)
        new_rows.append({
            "Title": job["title"],
            "Company": job["company"],
            "Location": job["location"],
            "Remote": job["remote"],
            "Salary Min": job["salary_min"],
            "Salary Max": job["salary_max"],
            "Source": job["source"],
            "Score": job["score"],
            "Skills Matched": ", ".join(job.get("matched", [])),
            "AI Reasoning": job.get("reasoning", ""),
            "Description": job["description"],
            "Apply Link": job["url"],
            "Search Titles": ", ".join(search_titles),
            "Found At": found_at,
            "Resume Status": "",
        })

    if new_rows:
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
        save_main_sheet(folder_path, df)
    return df


def remove_jobs(folder_path: str, keys_to_remove: set) -> pd.DataFrame:
    """keys_to_remove: set of (Title, Company) tuples to drop from the saved sheet."""
    df = load_main_sheet(folder_path)
    if df.empty:
        return df
    mask = df.apply(lambda r: (str(r["Title"]), str(r["Company"])) not in keys_to_remove, axis=1)
    df = df[mask].reset_index(drop=True)
    save_main_sheet(folder_path, df)
    return df


def mark_resume_status(folder_path: str, title: str, company: str, status: str):
    df = load_main_sheet(folder_path)
    mask = (df["Title"].astype(str) == str(title)) & (df["Company"].astype(str) == str(company))
    df.loc[mask, "Resume Status"] = status
    save_main_sheet(folder_path, df)


def save_approved_resume(folder_path: str, company: str, title: str, docx_bytes: bytes) -> Path:
    company_dir = workspace_dir(folder_path) / "resumes" / safe_folder_name(company)
    company_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_folder_name(title)}.docx"
    path = company_dir / filename
    path.write_bytes(docx_bytes)
    return path


def cv_text_path(folder_path: str) -> Path:
    """
    Stored in a hidden subfolder (dot-prefixed, so it won't show up in a
    normal Finder/Explorer listing of your job folder) rather than as a
    visible cv.txt sitting alongside your Excel sheet and resumes.
    """
    hidden_dir = workspace_dir(folder_path) / ".job_search_agent"
    hidden_dir.mkdir(exist_ok=True)
    return hidden_dir / "cv_cache.txt"


def save_cv_text(folder_path: str, cv_text: str):
    """Caches your CV so you don't have to re-paste it every session —
    stored out of sight rather than as a visible file in your folder."""
    if cv_text and cv_text.strip():
        cv_text_path(folder_path).write_text(cv_text, encoding="utf-8")


def load_cv_text(folder_path: str) -> str:
    path = cv_text_path(folder_path)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""
