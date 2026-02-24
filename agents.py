#!/usr/bin/env python3
"""
BOTCOIN Multi-Agent System with Configurable Models

Environment Variables:
- ORCHESTRATOR_MODEL: Main coordinator (default: zai-org-glm-5)
- ANSWER_SOLVER_MODEL: Question answering (default: same as orchestrator)
- ANSWER_CHECKER_MODEL: Answer verification (default: same as orchestrator)
- USE_EFFICIENT_MODE: true = 2-phase, false = multi-agent (default: true)
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
ANSWER_SOLVER_MODEL = os.environ.get("ANSWER_SOLVER_MODEL", ORCHESTRATOR_MODEL)
ANSWER_CHECKER_MODEL = os.environ.get("ANSWER_CHECKER_MODEL", ORCHESTRATOR_MODEL)

# Mode selection
USE_EFFICIENT_MODE = os.environ.get("USE_EFFICIENT_MODE", "true").lower() == "true"

MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "32000"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
CONCURRENT_SWARM = int(os.environ.get("CONCURRENT_SWARM", "1"))

# Phase-specific token caps for efficient 2-phase solver
PHASE1_MAX_TOKENS = int(os.environ.get("PHASE1_MAX_TOKENS", "6000"))
PHASE2_MAX_TOKENS = int(os.environ.get("PHASE2_MAX_TOKENS", "3000"))


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


class AgentA:
    """Answerer - Uses ANSWER_SOLVER_MODEL"""
    
    @staticmethod
    def answer_question(doc: str, question: str, companies: List[str]) -> Tuple[str, Dict]:
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

        print(f"  → Agent A ({ANSWER_SOLVER_MODEL[:20]}...) answering...", end=' ', flush=True)
        response, usage = call_llm(prompt, ANSWER_SOLVER_MODEL, max_tokens=500)
        
        # Extract company name
        for company in companies:
            if company.lower() in response.lower():
                print(f"→ {company}")
                return company, usage
        
        first_line = response.split('\n')[0].strip()
        print(f"→ {first_line}")
        return first_line, usage


class AgentB:
    """Verifier - Uses ANSWER_CHECKER_MODEL"""
    
    @staticmethod
    def verify_answer(doc: str, question: str, answer: str, companies: List[str]) -> Tuple[bool, Optional[str], Dict]:
        prompt = f"""Verify if this answer is correct.

DOCUMENT:
{doc}

QUESTION:
{question}

PROPOSED ANSWER:
{answer}

INSTRUCTIONS:
1. Verify against the document
2. If CORRECT, output EXACTLY: CORRECT
3. If WRONG, output EXACTLY: WRONG: CorrectAnswer (single line)
4. Do NOT add any extra explanation, text, or formatting

OUTPUT:"""

        print(f"  → Agent B ({ANSWER_CHECKER_MODEL[:20]}...) verifying...", end=' ', flush=True)
        response, usage = call_llm(prompt, ANSWER_CHECKER_MODEL, max_tokens=500)
        
        response_upper = response.upper().strip()
        
        if 'CORRECT' in response_upper and 'WRONG' not in response_upper:
            print("✓ CORRECT")
            return True, None, usage
        
        if ':' in response:
            corrected = response.split(':', 1)[1].strip()
            for company in companies:
                if company.lower() in corrected.lower():
                    print(f"✗ WRONG → {company}")
                    return False, company, usage
            print(f"✗ WRONG → {corrected}")
            return False, corrected, usage
        
        print("✗ UNCERTAIN")
        return False, None, usage


class Orchestrator:
    """Main coordinator - Uses ORCHESTRATOR_MODEL"""
    
    def __init__(self):
        self.answers = {}
        self.total_usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    
    def log(self, msg: str):
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] {msg}")
    
    def solve_question(self, doc: str, question_num: int, question: str, companies: List[str]) -> str:
        print(f"\n[Q{question_num}] {question[:60]}...")
        
        for attempt in range(MAX_RETRIES):
            # Agent A answers
            answer, usage_a = AgentA.answer_question(doc, question, companies)
            self.total_usage['prompt_tokens'] += usage_a['prompt_tokens']
            self.total_usage['completion_tokens'] += usage_a['completion_tokens']
            self.total_usage['total_tokens'] += usage_a['total_tokens']
            
            # Agent B verifies
            is_correct, correction, usage_b = AgentB.verify_answer(doc, question, answer, companies)
            self.total_usage['prompt_tokens'] += usage_b['prompt_tokens']
            self.total_usage['completion_tokens'] += usage_b['completion_tokens']
            self.total_usage['total_tokens'] += usage_b['total_tokens']
            
            if is_correct:
                return answer
            
            if correction:
                return correction
            
            print(f"  ⚠ Retry {attempt+2}/{MAX_RETRIES}...")
        
        print(f"  ⚠ Max retries, using: {answer}")
        return answer
    
    def solve_all_questions(self, doc: str, questions: List[str], companies: List[str]) -> Dict[int, str]:
        print(f"\n{'='*60}")
        print(f"MULTI-AGENT MODE ({CONCURRENT_SWARM} concurrent)")
        print(f"  Orchestrator: {ORCHESTRATOR_MODEL}")
        print(f"  Answerer: {ANSWER_SOLVER_MODEL}")
        print(f"  Checker: {ANSWER_CHECKER_MODEL}")
        print(f"{'='*60}\n")
        
        if CONCURRENT_SWARM > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            with ThreadPoolExecutor(max_workers=min(CONCURRENT_SWARM, len(questions))) as executor:
                futures = {
                    executor.submit(self.solve_question, doc, i+1, q, companies): i+1
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
                answer = self.solve_question(doc, q_num, question, companies)
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
        
        if USE_EFFICIENT_MODE:
            artifact = self._solve_efficient(doc, questions, constraints, companies, previous_artifact, failed_constraints)
            return artifact, {}
        else:
            return self._solve_multi_agent_smart(doc, questions, constraints, companies, 
                                                 previous_artifact, failed_constraints, previous_answers)
    
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
                    answer = self.solve_question(doc, q_num, questions[q_num-1], companies)
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
    
    def _solve_efficient(self, doc, questions, constraints, companies, previous_artifact, failed_constraints):
        """Efficient 2-phase approach using only ORCHESTRATOR_MODEL"""
        print(f"\n{'='*60}")
        print(f"EFFICIENT MODE (2-phase with {ORCHESTRATOR_MODEL})")
        print(f"{'='*60}")
        
        # Phase 1: Answer all questions
        print(f"\nPhase 1: Answering {len(questions)} questions...")
        phase1_prompt = f"""Answer all 10 questions from the document.

DOCUMENT:
{doc[:12000]}

QUESTIONS:
{chr(10).join([f"{i+1}. {q}" for i, q in enumerate(questions)])}

OUTPUT JSON:
{{"Q1": "Company", "Q2": "Company", ...}}

RESPONSE RULES:
1. Return a SINGLE valid JSON object exactly in the format above.
2. Keys MUST be Q1 through Q10.
3. Values MUST be plain company names (strings).
4. Do NOT include any explanation, markdown, or extra text before or after the JSON.

JSON ONLY:"""
        
        response1, usage1 = call_llm(phase1_prompt, ORCHESTRATOR_MODEL, max_tokens=PHASE1_MAX_TOKENS)
        self.total_usage['prompt_tokens'] += usage1['prompt_tokens']
        self.total_usage['completion_tokens'] += usage1['completion_tokens']
        self.total_usage['total_tokens'] += usage1['total_tokens']
        print(f"✓ Phase 1: {usage1['total_tokens']} tokens")
        
        # Parse answers
        try:
            json_start = response1.find('{')
            json_end = response1.rfind('}') + 1
            answers = json.loads(response1[json_start:json_end]) if json_start >= 0 else {}
        except:
            answers = {}
        
        # Phase 2: Construct artifact
        print(f"\nPhase 2: Constructing artifact...")
        phase2_prompt = f"""Construct artifact from answers.

ANSWERS:
{json.dumps(answers, indent=2)}

CONSTRAINTS:
{json.dumps(constraints, indent=2)}

INSTRUCTIONS:
1. Use the answers and constraints to construct the final artifact.
2. The artifact MUST satisfy ALL constraints exactly (including word counts, acrostics, and character rules).
3. Your FINAL RESPONSE MUST BE EXACTLY ONE LINE: the artifact string and nothing else.
4. Do NOT include labels, prefixes, explanations, JSON, or markdown.

OUTPUT ONLY THE SINGLE-LINE ARTIFACT:"""
        
        if previous_artifact and failed_constraints:
            phase2_prompt += f"\n\nFIX: Previous {previous_artifact} failed on {failed_constraints}"
        
        response2, usage2 = call_llm(phase2_prompt, ORCHESTRATOR_MODEL, max_tokens=PHASE2_MAX_TOKENS)
        self.total_usage['prompt_tokens'] += usage2['prompt_tokens']
        self.total_usage['completion_tokens'] += usage2['completion_tokens']
        self.total_usage['total_tokens'] += usage2['total_tokens']
        print(f"✓ Phase 2: {usage2['total_tokens']} tokens")
        
        artifact = response2.strip().split('\n')[-1].strip()
        print(f"\n✓ Total: {self.total_usage['total_tokens']} tokens")
        print(f"✓ Artifact: {artifact[:80]}... ({len(artifact.split())} words)")
        
        return artifact
