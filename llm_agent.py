"""
Optional AI layer for Step 1, built with Google's Agent Development Kit
(ADK) — running Claude (via LiteLLM) as the underlying model, not Gemini.
ADK supports any LiteLLM-compatible model through its LiteLlm wrapper;
this uses that to keep ADK's agent/session/runner structure while still
billing against your Anthropic API key.

This is entirely optional: agent.run_search() falls back to free keyword
matching automatically if no Anthropic API key is configured. When a key
is present, this does two things a keyword-matcher can't:

  1. analyze_resume()  — reads the CV like a person would: real skills
     (including implied ones, not just literal keyword hits), seniority,
     years of experience, and extra job titles worth searching that the
     person may not have typed themselves.
  2. score_jobs()       — judges genuine fit between the resume and each
     job (seniority match, real skill overlap, domain relevance) instead
     of just counting shared words.

Note: this makes two independent agent calls — it's not multi-agent
orchestration (that's what crew_resume.py's CrewAI Flow is for in Step 2).
"""

import asyncio
import json
import os

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

DEFAULT_MODEL = "anthropic/claude-sonnet-5"

ANALYZE_INSTRUCTION = (
    "You are an expert technical recruiter. Read the resume and respond "
    "with ONLY a JSON object (no markdown fences, no commentary) with "
    "these exact keys:\n"
    '{"skills": ["...", "..."], "years_experience": <int or null>, '
    '"seniority": "Junior" | "Mid" | "Senior" | "Lead/Principal", '
    '"suggested_titles": ["...", "..."]}\n'
    "skills: the real skills demonstrated in the resume, including ones "
    "implied by described work even if not literally named. "
    "years_experience: calculate this CAREFULLY from the actual employment "
    "date ranges listed in the work history (e.g. if the only listed job "
    "ran Jan 2024-Aug 2025, that is under 2 years of real experience, "
    "regardless of any other number that appears elsewhere in the resume "
    "text, such as a degree duration or a project length). Do not confuse "
    "years of education, project timelines, or company age with years of "
    "the candidate's own work experience. If dates are ambiguous or absent, "
    "return null rather than guessing high. "
    "seniority: base this strictly on years_experience and actual scope of "
    "responsibility shown — Junior/entry-level for under 2 years, Mid for "
    "2-5, Senior for 5-8, Lead/Principal for 8+ or explicit leadership "
    "scope. Do not round up. "
    "suggested_titles: 2-4 additional job titles (besides any given) this "
    "person is genuinely qualified for AT THEIR ACTUAL SENIORITY LEVEL — "
    "do not suggest senior/lead/principal titles for a junior candidate."
)

SCORE_INSTRUCTION = (
    "You are an expert technical recruiter judging job fit. You are given "
    "a candidate profile (including their real years_experience and "
    "seniority) and a list of jobs. For EACH job, judge genuine fit "
    "considering seniority match, real skill overlap, and domain "
    "relevance — not just shared keywords. "
    "SENIORITY MISMATCH IS A HARD FILTER, not a minor factor: if a job's "
    "title or description clearly requires meaningfully more experience "
    "than the candidate has (e.g. the listing says \"5+ years\" or is "
    "titled Senior/Lead/Principal/Staff/Director and the candidate has "
    "1-2 years, or is Junior-level), score that job 0-2 regardless of how "
    "many keywords overlap. A skills-only match at the wrong seniority is "
    "not a good fit and should not score above 3. "
    "Respond with ONLY a JSON array "
    "(no markdown fences, no commentary), one object per job, in this "
    'exact form: [{"index": <int>, "score": <0-10 int>, "reasoning": '
    '"<one short sentence>"}, ...]. Include every job index given to you '
    "exactly once."
)


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


async def _run_agent(instruction: str, prompt: str, anthropic_api_key: str, model: str) -> str:
    os.environ["ANTHROPIC_API_KEY"] = anthropic_api_key

    worker = Agent(
        model=LiteLlm(model=model),
        name="job_search_helper",
        instruction=instruction,
    )
    session_service = InMemorySessionService()
    runner = Runner(app_name="job_search_agent", agent=worker, session_service=session_service)
    session = await session_service.create_session(state={}, app_name="job_search_agent", user_id="user")

    content = types.Content(role="user", parts=[types.Part(text=prompt)])
    events = runner.run_async(session_id=session.id, user_id=session.user_id, new_message=content)

    output = ""
    async for event in events:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    output += part.text
    return output


def analyze_resume(cv_text: str, anthropic_api_key: str, model: str = DEFAULT_MODEL,
                    seed_titles=None) -> dict:
    """Returns {"skills": [...], "years_experience": int|None,
    "seniority": str, "suggested_titles": [...]}. Raises on failure —
    caller should catch and fall back to keyword extraction."""
    seed_note = f" The candidate already plans to search for: {', '.join(seed_titles)}." if seed_titles else ""
    prompt = f"RESUME:\n{cv_text}\n\n{seed_note}"
    raw = asyncio.run(_run_agent(ANALYZE_INSTRUCTION, prompt, anthropic_api_key, model))
    return json.loads(_strip_json_fences(raw))


def score_jobs(resume_profile: dict, jobs: list, anthropic_api_key: str,
               model: str = DEFAULT_MODEL) -> dict:
    """
    jobs: list of dicts with at least title, company, description.
    Returns {index: {"score": int, "reasoning": str}} keyed by list position.
    Raises on failure — caller should catch and fall back to keyword scoring.
    """
    job_lines = []
    for i, job in enumerate(jobs):
        desc = (job.get("description") or "")[:500]
        job_lines.append(f"[{i}] {job.get('title','')} at {job.get('company','')}: {desc}")

    prompt = (
        f"CANDIDATE PROFILE:\n{json.dumps(resume_profile)}\n\n"
        f"JOBS:\n" + "\n".join(job_lines)
    )
    raw = asyncio.run(_run_agent(SCORE_INSTRUCTION, prompt, anthropic_api_key, model))
    parsed = json.loads(_strip_json_fences(raw))
    return {item["index"]: {"score": item["score"], "reasoning": item.get("reasoning", "")}
            for item in parsed}
