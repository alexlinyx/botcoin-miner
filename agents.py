"""
BOTCOIN Multi-Agent Orchestration System

Architecture:
- Orchestrator: Breaks down challenge, coordinates agents, constructs artifact
- Agent A (Answerer): Answers individual questions
- Agent B (Verifier): Verifies answers, loops back if wrong
- CONCURRENT_SWARM: Controls parallel vs sequential execution
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
VENICE_MODEL = os.environ.get("VENICE_MODEL", "zai-org-glm-5")
VENICE_BASE_URL = os.environ.get("VENICE_BASE_URL", "https://api.venice.ai/api/v1")
CONCURRENT_SWARM = int(os.environ.get("CONCURRENT_SWARM", "1"))  # Number of parallel agents (1 = sequential)
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "16000"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))


def call_llm(prompt: str, max_tokens: int = None) -> Tuple[str, dict]:
    """Make LLM API call and return response with token usage."""
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
            "max_tokens": max_tokens or MAX_TOKENS if MAX_TOKENS > 0 else 16000,
            "stream": True,
        },
        stream=True,
        timeout=None,
    )
    
    if resp.status_code != 200:
        raise Exception(f"LLM error ({resp.status_code}): {resp.text[:200]}")
    
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
            
            reasoning = delta.get('reasoning_content')
            if reasoning:
                reasoning_chunks.append(reasoning)
            
            usage = chunk.get('usage', {})
            if usage:
                prompt_tokens = usage.get('prompt_tokens', prompt_tokens)
                completion_tokens = usage.get('completion_tokens', completion_tokens)
        except json.JSONDecodeError:
            continue
    
    content = ''.join(chunks).strip()
    reasoning = ''.join(reasoning_chunks).strip()
    
    if not content and reasoning:
        content = reasoning
    
    usage = {
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
        'total_tokens': prompt_tokens + completion_tokens
    }
    
    return content, usage


class AgentA:
    """Answerer Agent - Answers individual questions."""
    
    @staticmethod
    def answer_question(doc: str, question: str, companies: List[str]) -> Tuple[str, dict]:
        """Answer a single question by searching the document."""
        prompt = f"""Answer this question by finding the EXACT company name in the document.

DOCUMENT:
{doc}

VALID COMPANY NAMES (answer must match exactly one of these):
{json.dumps(companies, indent=2)}

QUESTION:
{question}

INSTRUCTIONS:
1. Search the document for information relevant to the question
2. Find the EXACT company name that answers it
3. Verify the company name matches one from the valid list above
4. Output ONLY the company name - nothing else

COMPANY:"""

        response, usage = call_llm(prompt, max_tokens=500)
        
        # Extract just the company name
        for company in companies:
            if company.lower() in response.lower():
                return company, usage
        
        # Return first word/line if no match
        first_line = response.split('\n')[0].strip()
        return first_line, usage


class AgentB:
    """Verifier Agent - Verifies answers and provides corrections."""
    
    @staticmethod
    def verify_answer(doc: str, question: str, answer: str, companies: List[str]) -> Tuple[bool, Optional[str], dict]:
        """Verify if answer is correct, return corrected answer if wrong."""
        prompt = f"""Verify if this answer is correct. If wrong, provide the correct answer.

DOCUMENT:
{doc}

VALID COMPANY NAMES:
{json.dumps(companies, indent=2)}

QUESTION:
{question}

PROPOSED ANSWER:
{answer}

INSTRUCTIONS:
1. Search the document to verify if the proposed answer is correct
2. If CORRECT, output: CORRECT
3. If WRONG, output: WRONG: CorrectCompanyName

OUTPUT:"""

        response, usage = call_llm(prompt, max_tokens=500)
        
        response_upper = response.upper().strip()
        
        if 'CORRECT' in response_upper and 'WRONG' not in response_upper:
            return True, None, usage
        
        # Extract correction
        if ':' in response:
            corrected = response.split(':', 1)[1].strip()
            # Match to valid company
            for company in companies:
                if company.lower() in corrected.lower():
                    return False, company, usage
            return False, corrected, usage
        
        return False, None, usage


class Orchestrator:
    """Orchestrator - Coordinates agents to solve the challenge."""
    
    def __init__(self):
        self.answers = {}
        self.total_usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    
    def log(self, msg: str):
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] {msg}")
    
    def solve_question(self, doc: str, question_num: int, question: str, companies: List[str]) -> str:
        """Solve a single question with verification loop."""
        # Print to terminal which question we're working on
        print(f"\n[Q{question_num}] {question[:80]}...")
        
        for attempt in range(MAX_RETRIES):
            # Agent A answers
            print(f"  → Agent A answering...", end='', flush=True)
            answer, usage_a = AgentA.answer_question(doc, question, companies)
            self.total_usage['prompt_tokens'] += usage_a['prompt_tokens']
            self.total_usage['completion_tokens'] += usage_a['completion_tokens']
            self.total_usage['total_tokens'] += usage_a['total_tokens']
            
            print(f" {answer}")
            self.log(f"  Q{question_num} Attempt {attempt+1}: Agent A → {answer}")
            
            # Agent B verifies
            print(f"  → Agent B verifying...", end='', flush=True)
            is_correct, correction, usage_b = AgentB.verify_answer(doc, question, answer, companies)
            self.total_usage['prompt_tokens'] += usage_b['prompt_tokens']
            self.total_usage['completion_tokens'] += usage_b['completion_tokens']
            self.total_usage['total_tokens'] += usage_b['total_tokens']
            
            if is_correct:
                print(f" ✓ CORRECT")
                self.log(f"  Q{question_num} ✓ Verified: {answer}")
                return answer
            
            if correction:
                print(f" ✗ WRONG → {correction}")
                self.log(f"  Q{question_num} ✗ Wrong. Corrected to: {correction}")
                return correction
            
            print(f" ✗ FAILED (retry {attempt+2}/{MAX_RETRIES})")
            self.log(f"  Q{question_num} ✗ Verification failed, retrying...")
        
        # Return best guess after max retries
        print(f"  ⚠ MAX RETRIES, using: {answer}")
        self.log(f"  Q{question_num} ⚠ Max retries reached, using: {answer}")
        return answer
    
    def solve_all_questions(self, doc: str, questions: List[str], companies: List[str]) -> Dict[int, str]:
        """Solve all questions using agent swarm."""
        mode = f"CONCURRENT={CONCURRENT_SWARM}" if CONCURRENT_SWARM > 1 else "SEQUENTIAL"
        print(f"\n{'='*60}")
        print(f"ORCHESTRATOR: Solving {len(questions)} questions ({mode} mode)")
        print(f"{'='*60}\n")
        
        if CONCURRENT_SWARM > 1:
            # Parallel execution with limited concurrency
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            max_workers = min(CONCURRENT_SWARM, len(questions))
            print(f"Spawning {max_workers} concurrent agent swarms...\n")
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
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
                        print(f"\n[Progress: {completed}/{len(questions)}] Q{q_num} completed")
                    except Exception as e:
                        self.log(f"  Q{q_num} ✗ Error: {e}")
                        self.answers[q_num] = "UNKNOWN"
        else:
            # Sequential execution
            for i, question in enumerate(questions):
                q_num = i + 1
                print(f"\n[Progress: {q_num}/{len(questions)}]")
                answer = self.solve_question(doc, q_num, question, companies)
                self.answers[q_num] = answer
        
        print(f"\n{'='*60}")
        print(f"All questions solved! Token usage: {self.total_usage['total_tokens']}")
        print(f"{'='*60}\n")
        return self.answers
    
    def construct_artifact(self, answers: Dict[int, str], constraints: List[str], previous_artifact: str = None, failed_constraints: List[int] = None) -> str:
        """Construct the final artifact from verified answers."""
        prompt = f"""Construct a single-line artifact that satisfies ALL constraints using these answers.

VERIFIED ANSWERS:
{json.dumps(answers, indent=2)}

CONSTRAINTS (artifact must satisfy ALL):
{json.dumps(constraints, indent=2)}

INSTRUCTIONS:
1. Read each constraint carefully
2. Use the answers above to extract required values
3. Construct a single-line artifact
4. COUNT WORDS EXACTLY
5. Verify all requirements are met

OUTPUT ONLY THE ARTIFACT - no other text."""

        if previous_artifact and failed_constraints:
            prompt += f"""

PREVIOUS ATTEMPT FAILED:
Previous artifact: "{previous_artifact}"
Failed constraints: {failed_constraints}

Fix these specific issues."""

        response, usage = call_llm(prompt, max_tokens=2000)
        
        self.total_usage['prompt_tokens'] += usage['prompt_tokens']
        self.total_usage['completion_tokens'] += usage['completion_tokens']
        self.total_usage['total_tokens'] += usage['total_tokens']
        
        # Extract single line
        lines = [l.strip() for l in response.split('\n') if l.strip()]
        if lines:
            for line in reversed(lines):
                if not any(line.upper().startswith(p) for p in 
                          ['Q1:', 'Q2:', 'CONSTRAINT', 'ANSWER', 'ARTIFACT:', 'OUTPUT']):
                    return line
        
        return response
    
    def solve_challenge(self, doc: str, questions: List[str], constraints: List[str], 
                       companies: List[str], previous_artifact: str = None, 
                       failed_constraints: List[int] = None) -> str:
        """Main orchestration: solve questions → construct artifact."""
        self.log("="*60)
        self.log("ORCHESTRATOR: Starting multi-agent solve")
        self.log("="*60)
        
        # Phase 1: Solve all questions
        answers = self.solve_all_questions(doc, questions, companies)
        
        # Phase 2: Construct artifact
        self.log("Constructing final artifact...")
        artifact = self.construct_artifact(answers, constraints, previous_artifact, failed_constraints)
        
        self.log(f"Artifact complete ({len(artifact.split())} words)")
        self.log(f"Total tokens used: {self.total_usage['total_tokens']}")
        
        return artifact
