import io
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import agent
import crew_resume
import workspace

st.set_page_config(page_title="Job Search Agent", page_icon="🧭", layout="wide")


def get_rapidapi_key():
    try:
        if "RAPIDAPI_KEY" in st.secrets:
            return st.secrets["RAPIDAPI_KEY"]
    except Exception:
        pass
    try:
        import config
        return config.RAPIDAPI_KEY
    except ImportError:
        return None


def get_anthropic_api_key():
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    try:
        import config
        return getattr(config, "ANTHROPIC_API_KEY", None)
    except ImportError:
        return None


COUNTRY_LABELS = {
    "us": "United States", "gb": "United Kingdom", "ca": "Canada",
    "au": "Australia", "de": "Germany", "fr": "France",
    "in": "India", "nl": "Netherlands", "sg": "Singapore",
}

DATE_POSTED_LABELS = {
    "all": "Any time", "today": "Past 24 hours", "3days": "Past 3 days",
    "week": "Past week", "month": "Past month",
}


def to_excel_bytes(df, sheet_name="Sheet1"):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        ws = writer.sheets[sheet_name]
        for i, col in enumerate(df.columns, start=1):
            width = 60 if col in ("Description", "AI Reasoning") else (40 if col == "Apply Link" else 16)
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    return buf.getvalue()


def clickable_title(title: str, url: str) -> str:
    """
    Streamlit's LinkColumn can only show the raw URL or a single fixed
    label for a whole column — it can't show a different, per-row custom
    text (like the actual job title) pulled from another column. This
    embeds the title as a URL fragment so a regex-based display_text can
    pull it back out for display, while the href still opens the real
    listing (the #fragment is harmless — browsers/servers ignore it).
    """
    if not url:
        return title
    return f"{url}#{title}"


TITLE_LINK_COLUMN = st.column_config.LinkColumn(
    "Title", display_text=r"#(.*)$", width="medium",
)


# --------------------------------------------------------------- SAVE LOCATION
st.title("Job Search Agent")

qp_folder = st.query_params.get("folder", "")
default_folder = qp_folder or str(Path.home() / "Desktop" / "Job")
folder_path = st.text_input(
    "Folder to save your job search files (a real folder path on your computer)",
    value=default_folder,
    help="This exact folder will contain main_matches.xlsx and a resumes/ subfolder. "
         "Point it at an existing folder (e.g. one on your Desktop) or a new path — it's created if missing.",
)
if folder_path and folder_path != qp_folder:
    st.query_params["folder"] = folder_path

if not folder_path.strip():
    st.info("Enter a folder path above to get started.")
    st.stop()

workspace.workspace_dir(folder_path)  # ensures it exists
st.caption(f"Saving to: `{Path(folder_path).expanduser()}`")

tab_search, tab_tailor = st.tabs(["Search & Select", "Tailor Resumes"])

# ------------------------------------------------------------------ SEARCH TAB
with tab_search:
    st.caption(
        "Upload your CV and the roles you're targeting. This pulls live listings originally posted "
        "on LinkedIn, Indeed, Glassdoor, ZipRecruiter, and more (via Google for Jobs), ranks them "
        "against your skills. Nothing is saved until you check boxes below and click Save."
    )

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("Your background")
        uploaded = st.file_uploader("Upload your CV", type=["txt", "pdf", "docx"])

        saved_cv = workspace.load_cv_text(folder_path)
        pasted = st.text_area("...or paste your CV text", height=180, value="" if uploaded else saved_cv)

        cv_text = ""
        if uploaded is not None:
            try:
                cv_text = agent.extract_text_from_upload(uploaded)
                st.success(f"Read {len(cv_text)} characters from {uploaded.name}")
            except Exception as e:
                st.error(str(e))
        elif pasted.strip():
            cv_text = pasted
        elif saved_cv:
            cv_text = saved_cv
            st.caption("Using the CV you saved in a previous session for this folder.")

        if cv_text.strip():
            workspace.save_cv_text(folder_path, cv_text)
            st.session_state["last_cv_text"] = cv_text

    with col2:
        st.subheader("Roles you're targeting")
        titles_raw = st.text_input("Job titles, comma-separated", placeholder="Software Engineer, AI Engineer")
        titles = [t.strip() for t in titles_raw.split(",") if t.strip()]

        row = st.columns(2)
        country = row[0].selectbox("Country", list(COUNTRY_LABELS.keys()), format_func=lambda c: COUNTRY_LABELS[c])
        date_posted = row[1].selectbox(
            "Posted within", list(DATE_POSTED_LABELS.keys()), format_func=lambda d: DATE_POSTED_LABELS[d],
        )
        location = st.text_input("Location filter (optional)", placeholder="remote, London, New York")
        results_per_title = st.slider(
            "Results to fetch per title", min_value=10, max_value=50, value=20, step=10,
            help="Higher = more listings, especially useful with narrow filters like 'Past 24 hours'. Costs more API calls.",
        )

    jsearch_key = get_rapidapi_key()
    anthropic_api_key = get_anthropic_api_key()
    google_key_ready = bool(anthropic_api_key) and anthropic_api_key != "your_anthropic_api_key_here"

    use_ai = st.checkbox(
        "Use AI agent (Claude) for smarter resume parsing, extra title suggestions, and real fit scoring",
        value=google_key_ready, disabled=not google_key_ready,
    )
    if not google_key_ready:
        st.caption("AI agent not configured — using free keyword matching instead. Add ANTHROPIC_API_KEY to config.py to enable it.")

    run = st.button("Find matching jobs", type="primary", use_container_width=True)

    if run:
        if not cv_text.strip():
            st.warning("Upload or paste your CV first.")
        elif not titles:
            st.warning("Add at least one target job title.")
        elif not jsearch_key or jsearch_key == "your_rapidapi_key_here":
            st.warning("The RapidAPI key isn't set up yet. See README.md.")
        else:
            with st.spinner("Searching listings and scoring matches..."):
                jobs, skills, years, errors = agent.run_search(
                    cv_text, titles, country, location, jsearch_key,
                    anthropic_api_key=anthropic_api_key if use_ai else None,
                    date_posted=date_posted, results_per_title=results_per_title,
                )
                st.session_state["last_jobs"] = jobs
                st.session_state["last_search_titles"] = titles
                st.session_state["last_cv_text"] = cv_text

            for err in errors:
                st.info(err)
            st.write(
                f"**Detected skills:** {', '.join(skills) if skills else 'none found'}  \n"
                f"**Detected experience:** {years if years else 'not found'} years  \n"
                f"**Matches found:** {len(jobs)}"
            )

    # ------------------------------------------------------- FRESH RESULTS
    fresh_jobs = st.session_state.get("last_jobs")
    if fresh_jobs:
        st.divider()
        st.subheader("Results from your last search")
        st.caption("Check the jobs you want to keep. Only checked rows are saved — everything else is discarded.")

        results_df = pd.DataFrame([{
            "Save": False,
            "Score": j["score"],
            "Title": clickable_title(j["title"], j["url"]),
            "Company": j["company"],
            "Location": j["location"],
            "Remote": j["remote"],
            "Source": j["source"],
            "Salary Min": j["salary_min"],
            "Salary Max": j["salary_max"],
            "Skills Matched": ", ".join(j.get("matched", [])),
            "AI Reasoning": j.get("reasoning", ""),
            "Description": j["description"][:200],
            "Apply Link": j["url"],
        } for j in fresh_jobs])

        edited_results = st.data_editor(
            results_df, use_container_width=True, hide_index=True,
            disabled=[c for c in results_df.columns if c != "Save"],
            column_config={
                "Save": st.column_config.CheckboxColumn(required=True),
                "Title": TITLE_LINK_COLUMN,
            },
            key="fresh_results_editor",
        )

        if st.button("Save checked jobs", type="primary"):
            checked_indices = edited_results.index[edited_results["Save"]].tolist()
            checked_jobs = [fresh_jobs[i] for i in checked_indices]
            if not checked_jobs:
                st.warning("Check at least one job first.")
            else:
                workspace.save_selected_jobs(
                    folder_path, checked_jobs, st.session_state.get("last_search_titles", []),
                    datetime.now().isoformat(timespec="seconds"),
                )
                st.success(f"Saved {len(checked_jobs)} job(s) to main_matches.xlsx.")
                del st.session_state["last_jobs"]
                st.rerun()

    # ------------------------------------------------------- SAVED SHORTLIST
    st.divider()
    st.subheader("Your saved shortlist")
    st.caption("Only jobs you've saved live here. Uncheck 'Keep' and click Update to remove one from the list.")

    main_df = workspace.load_main_sheet(folder_path)
    if main_df.empty:
        st.caption("Nothing saved yet — run a search above and check some boxes.")
    else:
        display_df = main_df.copy()
        display_df.insert(0, "Keep", True)
        display_df["Title"] = [
            clickable_title(t, u) for t, u in zip(display_df["Title"], display_df["Apply Link"])
        ]
        edited_main = st.data_editor(
            display_df, use_container_width=True, hide_index=True,
            disabled=[c for c in display_df.columns if c != "Keep"],
            column_config={
                "Keep": st.column_config.CheckboxColumn(required=True),
                "Title": TITLE_LINK_COLUMN,
            },
            key="shortlist_editor",
        )

        s1, s2 = st.columns(2)
        if s1.button("Update list (remove unchecked)"):
            unchecked_idx = edited_main.index[~edited_main["Keep"]]
            to_remove = set(zip(
                main_df.loc[unchecked_idx, "Title"].astype(str),
                main_df.loc[unchecked_idx, "Company"].astype(str),
            ))
            if to_remove:
                workspace.remove_jobs(folder_path, to_remove)
                st.success(f"Removed {len(to_remove)} job(s).")
                st.rerun()
            else:
                st.info("Nothing was unchecked.")

        s2.download_button(
            "Download main Excel sheet", data=to_excel_bytes(main_df, "Job Matches"),
            file_name="main_matches.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# ----------------------------------------------------------------- TAILOR TAB
with tab_tailor:
    st.caption(
        "For each job in your shortlist, CrewAI orchestrates a Writer agent and an "
        "ATS Reviewer agent automatically: it writes a draft, scores it, and — if the score "
        "is below your target — feeds the feedback back and tries again on its own, up to "
        "the attempt limit below. You can still manually Regenerate afterward if you want more."
    )

    anthropic_api_key = get_anthropic_api_key()
    google_key_ready = bool(anthropic_api_key) and anthropic_api_key != "your_anthropic_api_key_here"

    if not google_key_ready:
        st.warning("Resume tailoring needs an Anthropic API key (ANTHROPIC_API_KEY) in config.py — see README.md.")
    else:
        settings_col1, settings_col2 = st.columns(2)
        min_score = settings_col1.slider("Target ATS score", min_value=50, max_value=100, value=65, step=5)
        max_attempts = settings_col2.slider("Max automatic attempts", min_value=1, max_value=5, value=1)
        st.caption(
            "Defaults set for speed (1 pass, no automatic revising). Raise these if you want the "
            "agent to revise itself against a higher bar — but each extra attempt roughly doubles "
            "the wait, since it means another full Write + Review round."
        )

        st.caption(
            "Claude API usage is billed per token, not a daily request cap — each resume uses "
            "several calls (Writer + Reviewer, per round), so cost scales with how much you "
            "generate, but there's no arbitrary daily wall like Gemini's free tier had."
        )

        main_df = workspace.load_main_sheet(folder_path)

        cv_text_for_tailor = st.session_state.get("last_cv_text", "") or workspace.load_cv_text(folder_path)
        if not cv_text_for_tailor:
            st.warning("No CV found for this folder yet.")
            pasted_here = st.text_area(
                "Paste your CV text here to get started (only needed once per folder)",
                height=180, key="tailor_tab_cv_paste",
            )
            if pasted_here.strip():
                workspace.save_cv_text(folder_path, pasted_here)
                st.session_state["last_cv_text"] = pasted_here
                st.rerun()

        if not cv_text_for_tailor:
            pass  # handled above — nothing more to show until a CV is saved
        elif main_df.empty:
            st.caption("Your shortlist is empty. Save some jobs in the Search & Select tab first.")
        else:
            pending_jobs = []
            for _, row in main_df.iterrows():
                job_key = f"{row['Title']}__{row['Company']}"
                if row.get("Resume Status", "") != "Approved" and not st.session_state.get(f"resume_draft_{job_key}"):
                    pending_jobs.append((job_key, row))

            if pending_jobs:
                if st.button(f"Generate all {len(pending_jobs)} pending resumes in parallel", type="primary"):
                    import concurrent.futures

                    progress = st.progress(0.0, text="Starting...")
                    results = {}
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                        futures = {
                            executor.submit(
                                crew_resume.generate_with_orchestration,
                                cv_text_for_tailor,
                                {"title": row["Title"], "company": row["Company"], "description": row["Description"]},
                                anthropic_api_key, min_score=min_score, max_attempts=max_attempts,
                            ): job_key
                            for job_key, row in pending_jobs
                        }
                        done = 0
                        for future in concurrent.futures.as_completed(futures):
                            job_key = futures[future]
                            done += 1
                            try:
                                results[job_key] = future.result()
                            except Exception as e:
                                results[job_key] = {"error": str(e)}
                            progress.progress(done / len(pending_jobs), text=f"Finished {done}/{len(pending_jobs)}")

                    for job_key, result in results.items():
                        if "error" in result:
                            st.error(f"{job_key.replace('__', ' at ')}: {result['error']}")
                        else:
                            st.session_state[f"resume_draft_{job_key}"] = result
                    st.rerun()

            for _, row in main_df.iterrows():
                job_key = f"{row['Title']}__{row['Company']}"
                job = {"title": row["Title"], "company": row["Company"], "description": row["Description"]}
                status = row.get("Resume Status", "")

                with st.expander(f"{row['Title']} at {row['Company']}  —  {status or 'Not started'}"):
                    state_key = f"resume_draft_{job_key}"

                    if status == "Approved":
                        st.success("Approved and saved to your resumes folder.")
                        saved_path = workspace.workspace_dir(folder_path) / "resumes" / \
                            workspace.safe_folder_name(row["Company"]) / f"{workspace.safe_folder_name(row['Title'])}.docx"
                        if saved_path.exists():
                            st.download_button(
                                "Download saved resume", data=saved_path.read_bytes(),
                                file_name=saved_path.name, key=f"dl-approved-{job_key}",
                            )
                        continue

                    draft = st.session_state.get(state_key)

                    if not draft:
                        if st.button("Generate tailored resume", key=f"gen-{job_key}", type="primary"):
                            with st.spinner(f"Writer + ATS Reviewer looping (up to {max_attempts} rounds, target {min_score}/100)..."):
                                try:
                                    result = crew_resume.generate_with_orchestration(
                                        cv_text_for_tailor, job, anthropic_api_key,
                                        min_score=min_score, max_attempts=max_attempts,
                                    )
                                    st.session_state[state_key] = result
                                    draft = result
                                except Exception as e:
                                    st.error(f"Generation failed: {e}")
                    else:
                        if st.button("Reject & regenerate", key=f"regen-{job_key}"):
                            with st.spinner("Writer + ATS Reviewer agents working on a new draft..."):
                                try:
                                    result = crew_resume.generate_or_revise(
                                        cv_text_for_tailor, job, anthropic_api_key,
                                        previous_resume=draft["resume_text"], previous_feedback=draft["feedback"],
                                    )
                                    st.session_state[state_key] = result
                                    draft = result
                                except Exception as e:
                                    st.error(f"Generation failed: {e}")

                    if draft:
                        m1, m2 = st.columns(2)
                        m1.metric("ATS Score", f"{draft['ats_score']}/100")
                        if "attempts_used" in draft:
                            m2.metric("Automatic rounds used", f"{draft['attempts_used']}/{max_attempts}")
                        if draft.get("history"):
                            st.caption("Round-by-round: " + " → ".join(
                                f"attempt {h['attempt']}: {h['ats_score']}/100" for h in draft["history"]
                            ))
                        st.caption(f"Reviewer feedback: {draft['feedback']}")
                        st.text_area("Resume preview", draft["resume_text"], height=300, key=f"preview-{job_key}")

                        approve_col, reject_col = st.columns(2)
                        if approve_col.button("Approve & Save", key=f"approve-{job_key}", type="primary"):
                            docx_bytes = crew_resume.build_docx_bytes(draft["resume_text"], f"{row['Title']} — {row['Company']}")
                            workspace.save_approved_resume(folder_path, row["Company"], row["Title"], docx_bytes)
                            workspace.mark_resume_status(folder_path, row["Title"], row["Company"], "Approved")
                            st.success(f"Saved to resumes/{row['Company']}/{row['Title']}.docx")
                            st.rerun()
                        reject_col.caption("Not right yet? Click 'Reject & regenerate' above for a new draft.")
