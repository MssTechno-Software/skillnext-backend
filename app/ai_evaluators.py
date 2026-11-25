"""OpenAI answer evaluators (ported from the Pathway project).

Both functions return (evaluation_dict, raw_openai_response).
"""

import json
from functools import lru_cache

from openai import OpenAI

from app.config import settings


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    # timeout + max_retries cap the worst-case latency of one evaluation
    return OpenAI(api_key=settings.OPENAI_API_KEY, timeout=20.0, max_retries=1)


def _parse(response) -> dict:
    raw = response.choices[0].message.content
    if not raw:
        raise ValueError("OpenAI returned an empty response.")
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
    return json.loads(raw)


# ------------------------------------------------------------ self introduction
def evaluate_self_introduction(question: str, answer: str, expected_points: str):
    prompt = f"""You are an expert interview assessor evaluating a candidate's self-introduction response.
Your task is to evaluate the candidate ONLY using the information provided below.

QUESTION: {question}

EXPECTED POINTS: {expected_points}

CANDIDATE ANSWER: {answer}

GROUNDING RULES:

1. Use ONLY:
   * Question
   * Expected Points
   * Candidate Answer
2. Do NOT assume skills, experience, achievements, leadership ability, communication ability, confidence, or knowledge unless explicitly mentioned in the candidate answer.
3. Do NOT reward or penalize candidates for information that is not present.
4. If an expected point is not mentioned, consider it missing.
5. Evaluate both freshers and experienced candidates fairly.
6. For Impact:
   * Experienced candidates may demonstrate impact through achievements, responsibilities, measurable results, promotions, business outcomes, or leadership contributions.
   * Freshers may demonstrate impact through projects, internships, certifications, academic achievements, hackathons, leadership activities, volunteering, learning initiatives, or evidence of potential.
   * Do not penalize a fresher solely for lacking professional work experience.

SCORING DIMENSIONS:

1. Relevance — Measures whether the candidate stayed focused on answering the question asked and avoided unrelated information.
2. Completeness — Measures how many expected points or key details were covered in the response.
3. Structure — Measures how logically and professionally the answer is organized from start to finish.
4. Professionalism — Measures the use of professional language, tone, and workplace-appropriate communication.
5. Impact — Measures how effectively the candidate presents their value, achievements, contributions, potential, or strengths.

SCORING RULES:

* Each dimension must be scored between 0 and 100.
* Be strict but fair.
* Base scores only on evidence found in the answer.
* Do not invent missing information.
* Overall score should reflect the weighted average below:

Overall Score Weighting:
* Relevance: 20%
* Completeness: 40%
* Structure: 15%
* Professionalism: 15%
* Impact: 10%

CANDIDATE TIER RULES:
90-100 = Excellent | 75-89 = Strong | 60-74 = Good | 40-59 = Average | 0-39 = Needs Improvement

RETURN ONLY VALID JSON IN THE FOLLOWING FORMAT:
{{
    "overall_score": 0,
    "scores": {{
        "relevance": 0,
        "completeness": 0,
        "structure": 0,
        "professionalism": 0,
        "impact": 0
    }},
    "matched_points": [],
    "missing_points": [],
    "strengths": [],
    "improvement_areas": [],
    "overall_feedback": "",
    "candidate_tier": ""
}}"""

    response = _client().chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "You are an expert interview evaluator. Return only JSON."},
            {"role": "user", "content": prompt},
        ],
    )
    return _parse(response), response


# ---------------------------------------------------------------- course theory
def evaluate_theory_answer(question: str, reference_answer: str, answer: str):
    prompt = f"""You are an expert technical interviewer evaluating a candidate's answer to a theory question.
Your task is to evaluate the candidate ONLY using the information provided below.

QUESTION: {question}

REFERENCE ANSWER: {reference_answer}

CANDIDATE ANSWER: {answer}

GROUNDING RULES:
- Base your evaluation strictly on how well the candidate answer covers the concepts in the reference answer.
- Do not invent facts about the candidate that are not present in their answer.
- Be fair but rigorous, like a real technical interviewer.
- "matched_points" and "missing_points" should be specific concepts/keywords drawn from the REFERENCE ANSWER — list which ones the candidate did and did not cover.

Evaluate the candidate's answer and respond ONLY with valid JSON in this exact structure, nothing else:

{{
  "overall_score": <integer 0-100>,
  "scores": {{
    "accuracy": <integer 0-100>,
    "completeness": <integer 0-100>,
    "clarity": <integer 0-100>,
    "depth": <integer 0-100>
  }},
  "matched_points": ["concept from reference answer the candidate covered", "..."],
  "missing_points": ["concept from reference answer the candidate missed", "..."],
  "strengths": ["specific strength in the candidate's answer", "..."],
  "improvement_areas": ["specific improvement the candidate should make", "..."],
  "overall_feedback": "<2-3 sentence summary of the candidate's answer quality>",
  "candidate_tier": "<one of: Excellent, Good, Average, Needs Improvement, Poor>"
}}
"""

    response = _client().chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": "You are a precise technical interview evaluator that responds only in valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    return _parse(response), response