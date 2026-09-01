# Job Search Agent

Upload your CV and target job titles, get ranked live job listings —
originally posted on LinkedIn, Indeed, Glassdoor, ZipRecruiter, and more —
saved into your own workspace folder as an Excel file. Select the jobs
you want, and get an ATS-optimized, tailored resume for each — generated
and refined by a CrewAI team until you approve it.

## Stack

- **UI:** Streamlit
- **Job data:** [JSearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch) (RapidAPI) — reads Google for Jobs' public index, surfacing postings originally listed on LinkedIn, Indeed, Glassdoor, ZipRecruiter, Monster, and more. Free tier, no card required.
- **Resume/job analysis (Step 1):** **Google ADK** running **Claude** (via LiteLLM, not Gemini) — optional; falls back to free local keyword matching if no key is set.
- **Resume tailoring (Step 2):** **CrewAI** (running Claude), orchestrated with a **CrewAI Flow** — not just repeated manual clicks:
  - **ATS Resume Writer** — rewrites your resume for a specific job, truthfully
  - **ATS Reviewer** — scores it 0–100 and gives concrete feedback
  - The **Flow itself decides** whether to stop or loop: if the score is below your target, it automatically feeds the reviewer's feedback back to the writer and tries again — up to a max attempt limit you set. No clicking between rounds; that's the actual orchestration.
  - Once the automatic loop finishes, you can still manually **Regenerate** for further passes, and nothing is saved until you click **Approve**.
- **Storage:** per-workspace folders (see below) — no database, just files.

### Why not scrape LinkedIn/Indeed/Glassdoor directly?

Their Terms of Service prohibit automated scraping and they actively block
it. JSearch reads data Google already indexes publicly instead.

## Where your files are saved

At the top of the app, enter a **real folder path** on your computer —
e.g. `/Users/you/Desktop/Job`. Everything goes exactly there:

```
<your chosen folder>/
├── main_matches.xlsx      # ONLY the jobs you've checked and saved —
│                          # unchecked search results are never written here
└── resumes/
    └── <Company Name>/
        └── <Job Title>.docx   # approved, tailored resumes
```

Bookmark the app's URL after entering a path — it's stored in the URL
(`?folder=...`), so returning to that link brings the same path back.

**How saving works, step by step:**
1. Run a search — results appear in a table with a **Save** checkbox,
   all unchecked by default.
2. Check the ones you want, click **Save checked jobs** — only those
   rows get written to `main_matches.xlsx`. Anything left unchecked is
   discarded and never touches the file.
3. Your **saved shortlist** (further down the page) shows everything
   you've saved so far, with a **Keep** checkbox — uncheck one and
   click **Update list** to remove it from the file entirely.
4. The **Tailor Resumes** tab works directly off this shortlist.

## Setup

1. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate    # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. Get a free RapidAPI key for JSearch: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
   (sign up, subscribe to the free "Basic" plan, copy your key)
3. Get an Anthropic API key: https://console.anthropic.com/settings/keys
   (powers both the Step 1 AI agent and CrewAI's resume tailoring; new
   accounts get a small free trial credit, then it's pay-per-token —
   see the Anthropic Console for current pricing)
4. Open `config.py` and fill in both:
   ```python
   RAPIDAPI_KEY = "your_actual_key_here"
   ANTHROPIC_API_KEY = "your_actual_anthropic_key_here"
   ```

## Run

```bash
streamlit run app.py
```

## Using it

1. **Enter a folder path** at the top — e.g. `~/Desktop/Job`. This is
   created automatically if it doesn't exist yet.
2. **Search & Select tab:** upload your CV, list job titles, pick a
   country, choose how recent the postings should be ("Posted within"),
   and click **Find matching jobs**.
3. In the results table, **check the jobs you want**, then click
   **Save checked jobs**. Only those are written to `main_matches.xlsx`.
4. Your saved shortlist appears below — uncheck **Keep** and click
   **Update list** to drop a job you've changed your mind about.
5. **Tailor Resumes tab:** set your **target ATS score** and **max
   automatic attempts** (sliders), then click **Generate tailored
   resume** for any shortlisted job. A CrewAI Flow runs the Writer →
   Reviewer loop on its own — you'll see how many rounds it took and
   the score at each round. Once it stops, click **Regenerate** for an
   additional manual pass, or **Approve & Save** to write a `.docx`
   into `resumes/<Company>/<Job Title>.docx` in your chosen folder.

## Notes

- Nothing leaves your machine except: job search queries to JSearch,
  and — for the AI agent and resume tailoring — your CV text and job
  descriptions sent to Anthropic's Claude API.
- The free tiers of both APIs have rate/quota limits.
- `config.py` (your keys) is excluded from git via `.gitignore` — never
  commit it. On Streamlit Cloud, use the Secrets manager instead.
- `data/workspaces/` is also excluded from git — it's personal data
  that shouldn't be shared via the repo.

## Project structure

```
job_search_agent/
├── app.py           # Streamlit UI — Search & Select, Tailor Resumes tabs
├── agent.py         # CV parsing, JSearch job search, scoring
├── llm_agent.py     # Google ADK + Claude (via LiteLLM): richer resume analysis, job scoring
├── crew_resume.py   # CrewAI Flow: Writer + ATS Reviewer, orchestrated revision loop
├── workspace.py     # Manages files at whatever folder path you choose
├── config.py        # Your API keys go here
└── requirements.txt

<your chosen folder>/    # created wherever you point the app — not inside this repo
├── main_matches.xlsx
└── resumes/
```
