#!/usr/bin/env python3
"""
BOTCOIN Orchestrator + Solver System

Environment Variables:
- ORCHESTRATOR_MODEL: Main coordinator (default: zai-org-glm-5)
- SOLVER_MODEL_PRIMARY: Primary solver for per-question answering (default: ORCHESTRATOR_MODEL)
- SOLVER_MODEL_BACKUP: Backup solver used only when fixing failed constraints (default: SOLVER_MODEL_PRIMARY)
"""

import os
import json
import time
import requests
from typing import Dict, List, Tuple, Optional
from dotenv import load_dotenv

load_dotenv()

# Configuration
VENICE_API_KEY = os.environ.get("VENICE_API_KEY")
VENICE_BASE_URL = os.environ.get("VENICE_BASE_URL", "https://api.venice.ai/api/v1")

# Model assignments per role
ORCHESTRATOR_MODEL = os.environ.get("ORCHESTRATOR_MODEL", "zai-org-glm-5")

# Primary/backup solver models
SOLVER_MODEL_PRIMARY = os.environ.get("SOLVER_MODEL_PRIMARY", ORCHESTRATOR_MODEL)
SOLVER_MODEL_BACKUP = os.environ.get("SOLVER_MODEL_BACKUP", SOLVER_MODEL_PRIMARY)

MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "32000"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
CONCURRENT_SWARM = int(os.environ.get("CONCURRENT_SWARM", "1"))


def call_llm(prompt: str, model: str, max_tokens: int = None, stream: bool = False) -> Tuple[str, Dict]:
    """Call LLM with specified model."""
    resp = requests.post(
        f"{VENICE_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {VENICE_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": max_tokens or MAX_TOKENS,
            "stream": stream,
        },
        timeout=300,
    )
    
    if resp.status_code != 200:
        raise Exception(f"LLM error ({resp.status_code}): {resp.text[:200]}")
    
    if stream:
        # Handle streaming response
        chunks = []
        reasoning_chunks = []
        prompt_tokens = 0
        completion_tokens = 0
        
        for line in resp.iter_lines():
            if not line:
                continue
            line = line.decode('utf-8')
            if not line.startswith('data: '):
                continue
            data = line[6:]
            if data == '[DONE]':
                break
            try:
                chunk = json.loads(data)
                delta = chunk.get('choices', [{}])[0].get('delta', {})
                content = delta.get('content')
                if content:
                    chunks.append(content)
                    print(content, end='', flush=True)
                reasoning = delta.get('reasoning_content')
                if reasoning:
                    reasoning_chunks.append(reasoning)
                usage = chunk.get('usage', {})
                if usage:
                    prompt_tokens = usage.get('prompt_tokens', prompt_tokens)
                    completion_tokens = usage.get('completion_tokens', completion_tokens)
            except:
                continue
        
        print()  # Newline after streaming
        content = ''.join(chunks).strip()
        if not content and reasoning_chunks:
            content = ''.join(reasoning_chunks).strip()
        
        return content, {
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens,
            'total_tokens': prompt_tokens + completion_tokens
        }
    else:
        # Non-streaming
        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        usage = result.get("usage", {})
        return content, usage


class Solver:
    """Per-question solver. Takes a question and returns a single company-name answer."""

    def __init__(self, model: str, mode_label: str):
        self.model = model
        self.mode_label = mode_label

    def answer_question(
        self,
        doc: str,
        question_num: int,
        question: str,
        companies: List[str],
    ) -> Tuple[str, Dict]:
        prompt = f"""Answer this question by finding the EXACT company name in the document.

DOCUMENT:
{doc}

VALID COMPANY NAMES:
{json.dumps(companies, indent=2)}

QUESTION:
{question}

INSTRUCTIONS:
1. Search the document carefully
2. Find the EXACT company name from the valid list
3. Output EXACTLY ONE LINE with ONLY the company name
4. Do NOT include quotes, explanations, or any other text

COMPANY NAME:"""

        print(f"  → Solver.{question_num} ({self.mode_label}: {self.model[:20]}...) answering...", end='', flush=True)
        response, usage = call_llm(prompt, self.model, max_tokens=500)

        # Extract company name
        for company in companies:
            if company.lower() in response.lower():
                print(f" → {company}")
                return company, usage

        first_line = response.split('\n')[0].strip()
        print(f" → {first_line}")
        return first_line, usage


class Orchestrator:
    """Main coordinator - Uses ORCHESTRATOR_MODEL"""
    
    def __init__(self):
        self.answers: Dict[int, str] = {}
        self.total_usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
        # Per-question solvers
        self.primary_solver = Solver(SOLVER_MODEL_PRIMARY, "primary")
        self.backup_solver = Solver(SOLVER_MODEL_BACKUP, "backup")
    
    def log(self, msg: str):
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] {msg}")
    
    def solve_question(self, doc: str, question_num: int, question: str, companies: List[str], use_backup: bool = False) -> str:
        """
        Solve a single question by delegating to the Solver.
        No separate verification step; the solver's first answer is used.
        """
        print(f"\n[Q{question_num}] {question[:60]}...")

        solver = self.backup_solver if use_backup else self.primary_solver
        answer, usage = solver.answer_question(doc, question_num, question, companies)

        # Track usage
        self.total_usage['prompt_tokens'] += usage.get('prompt_tokens', 0)
        self.total_usage['completion_tokens'] += usage.get('completion_tokens', 0)
        self.total_usage['total_tokens'] += usage.get('total_tokens', 0)

        return answer
    
    def solve_all_questions(self, doc: str, questions: List[str], companies: List[str]) -> Dict[int, str]:
        print(f"\n{'='*60}")
        print(f"MULTI-AGENT MODE ({CONCURRENT_SWARM} concurrent)")
        print(f"  Orchestrator: {ORCHESTRATOR_MODEL}")
        print(f"  Solver (primary): {SOLVER_MODEL_PRIMARY}")
        print(f"  Solver (backup): {SOLVER_MODEL_BACKUP}")
        print(f"{'='*60}\n")
        
        if CONCURRENT_SWARM > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            with ThreadPoolExecutor(max_workers=min(CONCURRENT_SWARM, len(questions))) as executor:
                futures = {
                    executor.submit(self.solve_question, doc, i+1, q, companies, False): i+1
                    for i, q in enumerate(questions)
                }
                
                completed = 0
                for future in as_completed(futures):
                    q_num = futures[future]
                    completed += 1
                    try:
                        answer = future.result()
                        self.answers[q_num] = answer
                        print(f"\n[Progress: {completed}/{len(questions)}] Q{q_num} done")
                    except Exception as e:
                        print(f"\n[Q{q_num}] Error: {e}")
                        self.answers[q_num] = "UNKNOWN"
        else:
            for i, question in enumerate(questions):
                q_num = i + 1
                print(f"\n[Progress: {q_num}/{len(questions)}]")
                answer = self.solve_question(doc, q_num, question, companies, False)
                self.answers[q_num] = answer
        
        print(f"\n✓ All questions solved. Tokens: {self.total_usage['total_tokens']}")
        return self.answers
    
    def construct_artifact(self, answers: Dict[int, str], constraints: List[str], 
                          previous_artifact: str = None, failed_constraints: List[int] = None) -> str:
        print(f"\n→ Constructing artifact with {ORCHESTRATOR_MODEL}...")
        
        prompt = f"""Construct artifact from verified answers.

ANSWERS:
{json.dumps(answers, indent=2)}

CONSTRAINTS:
{json.dumps(constraints, indent=2)}

INSTRUCTIONS:
1. Use the answers and constraints to build the final artifact.
2. The artifact MUST satisfy ALL constraints exactly (including word counts, acrostics, and character rules).
3. Your FINAL RESPONSE MUST BE EXACTLY ONE LINE: the artifact string and nothing else.
4. Do NOT include labels, prefixes, or explanations. No JSON, no markdown, no extra lines.

OUTPUT ONLY THE SINGLE-LINE ARTIFACT:"""

        if previous_artifact and failed_constraints:
            prompt += f"""

PREVIOUS FAILED: {previous_artifact}
FAILED CONSTRAINTS: {failed_constraints}"""

        response, usage = call_llm(prompt, ORCHESTRATOR_MODEL, max_tokens=2000)
        
        self.total_usage['prompt_tokens'] += usage['prompt_tokens']
        self.total_usage['completion_tokens'] += usage['completion_tokens']
        self.total_usage['total_tokens'] += usage['total_tokens']
        
        lines = [l.strip() for l in response.split('\n') if l.strip()]
        artifact = lines[-1] if lines else response.strip()
        
        print(f"✓ Artifact: {artifact[:80]}... ({len(artifact.split())} words)")
        return artifact
    
    def solve_challenge(self, doc: str, questions: List[str], constraints: List[str], 
                       companies: List[str], previous_artifact: str = None, 
                       failed_constraints: List[int] = None, previous_answers: Dict = None) -> Tuple[str, Dict]:
        """
        Main entry point with smart retry logic.
        On retry: Only re-solves questions related to failed constraints.
        """
        return self._solve_multi_agent_smart(
            doc,
            questions,
            constraints,
            companies,
            previous_artifact,
            failed_constraints,
            previous_answers,
        )
    
    def _solve_multi_agent(self, doc, questions, constraints, companies, previous_artifact, failed_constraints):
        """Original multi-agent - solve all questions."""
        self.solve_all_questions(doc, questions, companies)
        artifact = self.construct_artifact(self.answers, constraints, previous_artifact, failed_constraints)
        return artifact, self.answers
    
    def _solve_multi_agent_smart(self, doc, questions, constraints, companies, 
                                  previous_artifact, failed_constraints, previous_answers):
        """
        Multi-agent with intelligent retry:
        - First attempt: Solve all questions
        - Retry: Only solve questions related to failed constraints
        """
        
        # Determine which questions need solving
        if previous_answers and failed_constraints:
            # Smart retry: Map failed constraints to questions
            questions_to_resolve = self._map_constraints_to_questions(failed_constraints, constraints)
            
            print(f"\n{'='*60}")
            print(f"SMART RETRY: {len(questions_to_resolve)} questions need re-solving")
            print(f"Failed constraints: {failed_constraints}")
            print(f"{'='*60}\n")
            
            # Copy previous answers
            self.answers = previous_answers.copy()
            
            # Only re-solve failed questions
            for q_num in questions_to_resolve:
                if 1 <= q_num <= len(questions):
                    print(f"\n[Re-solving Q{q_num}] Previous: {self.answers.get(q_num, 'N/A')}")
                    # Use backup solver model on retry for these questions
                    answer = self.solve_question(doc, q_num, questions[q_num-1], companies, use_backup=True)
                    self.answers[q_num] = answer
                    print(f"[Updated Q{q_num}] New: {answer}")
        else:
            # First attempt: Solve all questions
            self.solve_all_questions(doc, questions, companies)
        
        # Construct artifact with all answers
        artifact = self.construct_artifact(self.answers, constraints, previous_artifact, failed_constraints)
        
        return artifact, self.answers
    
    def _map_constraints_to_questions(self, failed_constraints: List[int], constraints: List[str]) -> List[int]:
        """
        Map failed constraint indices to question numbers.
        This is heuristic-based on typical BOTCOIN constraint patterns.
        """
        questions_to_resolve = set()
        
        for constraint_idx in failed_constraints:
            if constraint_idx < len(constraints):
                constraint = constraints[constraint_idx].lower()
                
                # Map based on constraint content
                if 'q1' in constraint or 'question 1' in constraint or 'q1 answer' in constraint:
                    questions_to_resolve.add(1)
                if 'q2' in constraint or 'question 2' in constraint or 'q2 answer' in constraint:
                    questions_to_resolve.add(2)
                if 'q3' in constraint or 'question 3' in constraint or 'q3 answer' in constraint:
                    questions_to_resolve.add(3)
                if 'q4' in constraint or 'question 4' in constraint or 'q4 answer' in constraint:
                    questions_to_resolve.add(4)
                if 'q5' in constraint or 'question 5' in constraint or 'q5 answer' in constraint:
                    questions_to_resolve.add(5)
                if 'q6' in constraint or 'question 6' in constraint or 'q6 answer' in constraint:
                    questions_to_resolve.add(6)
                if 'q7' in constraint or 'question 7' in constraint or 'q7 answer' in constraint:
                    questions_to_resolve.add(7)
                if 'q8' in constraint or 'question 8' in constraint or 'q8 answer' in constraint:
                    questions_to_resolve.add(8)
                if 'q9' in constraint or 'question 9' in constraint or 'q9 answer' in constraint:
                    questions_to_resolve.add(9)
                if 'q10' in constraint or 'question 10' in constraint or 'q10 answer' in constraint:
                    questions_to_resolve.add(10)
        
        # If we couldn't map, re-solve last 3 questions as fallback
        if not questions_to_resolve:
            print("⚠ Could not map constraints to questions, using fallback (Q8, Q9, Q10)")
            questions_to_resolve = {8, 9, 10}
        
        return sorted(list(questions_to_resolve))
    
    def _solve_multi_agent(self, doc, questions, constraints, companies, previous_artifact, failed_constraints):
        """Original multi-agent approach"""
        answers = self.solve_all_questions(doc, questions, companies)
        artifact = self.construct_artifact(answers, constraints, previous_artifact, failed_constraints)
        return artifact
    
