#!/usr/bin/env python3
"""
Efficient BOTCOIN Solver - Uses single powerful model with structured prompting.
Minimizes API calls while maintaining accuracy.
"""

import os
import json
import time
import requests
from typing import Dict, List, Tuple
from dotenv import load_dotenv

load_dotenv()

VENICE_API_KEY = os.environ.get("VENICE_API_KEY")
VENICE_MODEL = os.environ.get("VENICE_MODEL", "zai-org-glm-5")
VENICE_BASE_URL = os.environ.get("VENICE_BASE_URL", "https://api.venice.ai/api/v1")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "32000"))

# Phase-specific token caps for 2-phase solver
PHASE1_MAX_TOKENS = int(os.environ.get("PHASE1_MAX_TOKENS", "8000"))
PHASE2_MAX_TOKENS = int(os.environ.get("PHASE2_MAX_TOKENS", "4000"))


def call_llm(prompt: str, max_tokens: int = None) -> Tuple[str, Dict]:
    """Make single LLM call."""
    resp = requests.post(
        f"{VENICE_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {VENICE_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": VENICE_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": max_tokens or MAX_TOKENS,
            "stream": False,  # Non-streaming for reliability
        },
        timeout=300,
    )
    
    if resp.status_code != 200:
        raise Exception(f"LLM error: {resp.text[:200]}")
    
    result = resp.json()
    content = result["choices"][0]["message"]["content"]
    usage = result.get("usage", {})
    
    return content, usage


def solve_challenge_efficient(doc: str, questions: List[str], constraints: List[str], 
                               companies: List[str]) -> Tuple[str, Dict]:
    """
    Efficient 2-phase approach:
    Phase 1: Answer all 10 questions in ONE API call
    Phase 2: Verify and construct artifact in ONE API call
    """
    
    print(f"\n{'='*60}")
    print(f"PHASE 1: Answering all {len(questions)} questions...")
    print(f"{'='*60}\n")
    
    # Phase 1: Single call to answer all questions
    phase1_prompt = f"""You are a precise document analyst. Read the document and answer ALL questions.

DOCUMENT (analyze carefully):
{doc[:15000]}  # First 15K chars to save tokens

VALID COMPANY NAMES (must match exactly):
{json.dumps(companies, indent=2)}

QUESTIONS (answer all 10):
{chr(10).join([f"{i+1}. {q}" for i, q in enumerate(questions)])}

INSTRUCTIONS:
1. Search the document for each question
2. Find the EXACT company name from the valid list
3. Be precise - verify your answer matches the document

OUTPUT FORMAT (JSON):
{{
  "Q1": "ExactCompanyName",
  "Q2": "ExactCompanyName",
  ... through Q10
}}

RESPONSE RULES:
1. Return a SINGLE valid JSON object exactly in the format above.
2. Keys MUST be Q1 through Q10.
3. Values MUST be plain company names (strings) from the valid list.
4. Do NOT include explanations, markdown, or any extra text.

JSON OUTPUT ONLY:"""

    print("→ Calling LLM to answer all questions...")
    response1, usage1 = call_llm(phase1_prompt, max_tokens=PHASE1_MAX_TOKENS)
    print(f"✓ Phase 1 complete ({usage1.get('total_tokens', 0)} tokens)")
    
    # Parse answers
    try:
        # Extract JSON from response
        json_start = response1.find('{')
        json_end = response1.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            answers = json.loads(response1[json_start:json_end])
        else:
            raise ValueError("No JSON found")
        
        print(f"\nAnswers extracted:")
        for q, a in answers.items():
            print(f"  {q}: {a}")
    except Exception as e:
        print(f"⚠ Failed to parse JSON: {e}")
        print(f"Raw response: {response1[:500]}")
        answers = {}
    
    print(f"\n{'='*60}")
    print(f"PHASE 2: Verifying and constructing artifact...")
    print(f"{'='*60}\n")
    
    # Phase 2: Single call to verify and construct
    phase2_prompt = f"""Using these answers, verify each one and construct the artifact.

ANSWERS:
{json.dumps(answers, indent=2)}

CONSTRAINTS:
{json.dumps(constraints, indent=2)}

INSTRUCTIONS:
1. Verify each answer against the constraints.
2. Extract required values (cities, names, numbers).
3. Calculate any arithmetic needed.
4. Construct a single-line artifact satisfying ALL constraints.
5. Verify word count EXACTLY.
6. Your FINAL RESPONSE MUST BE EXACTLY ONE LINE: the artifact string and nothing else.
7. Do NOT include labels, prefixes, explanations, JSON, or markdown.

OUTPUT ONLY THE SINGLE-LINE ARTIFACT:"""

    print("→ Calling LLM to construct artifact...")
    response2, usage2 = call_llm(phase2_prompt, max_tokens=PHASE2_MAX_TOKENS)
    print(f"✓ Phase 2 complete ({usage2.get('total_tokens', 0)} tokens)")
    
    # Extract artifact
    lines = [l.strip() for l in response2.split('\n') if l.strip()]
    artifact = lines[-1] if lines else response2.strip()
    
    print(f"\nArtifact: {artifact[:100]}...")
    print(f"Word count: {len(artifact.split())}")
    
    # Combine usage
    total_usage = {
        'prompt_tokens': usage1.get('prompt_tokens', 0) + usage2.get('prompt_tokens', 0),
        'completion_tokens': usage1.get('completion_tokens', 0) + usage2.get('completion_tokens', 0),
        'total_tokens': usage1.get('total_tokens', 0) + usage2.get('total_tokens', 0)
    }
    
    print(f"\nTotal tokens: {total_usage['total_tokens']}")
    
    return artifact, total_usage


if __name__ == "__main__":
    # Test with sample data
    print("Efficient solver ready!")
    print("Usage: Import solve_challenge_efficient() in miner.py")
