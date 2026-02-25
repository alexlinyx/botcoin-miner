#!/usr/bin/env python3
"""
BOTCOIN Miner - Mines BOTCOIN by solving AI challenges on Base.
Requires BANKR_API_KEY for wallet operations and on-chain transactions.
"""

import os
import json
import time
import random
import hashlib
import requests
from typing import Optional, Dict, Any

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Try to import cloudscraper for Cloudflare bypass
try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

# Configuration
COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "https://coordinator.agentmoney.net")
BANKR_API_KEY = os.environ.get("BANKR_API_KEY")
VENICE_API_KEY = os.environ.get("VENICE_API_KEY")  # Venice AI API key
USE_CLOUDSCRAPER = os.environ.get("USE_CLOUDSCRAPER", "true").lower() == "true"

# Model configuration
MODEL = os.environ.get("MODEL", "qwen3-235b-a22b-thinking-2507")

# Legacy support
VENICE_MODEL = os.environ.get("VENICE_MODEL", MODEL)
VENICE_BASE_URL = os.environ.get("VENICE_BASE_URL", "https://api.venice.ai/api/v1")

# Self-correction settings
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "0"))  # 0 = unlimited
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "0"))  # 0 = no timeout

# Botcoin token address
BOTCOIN_ADDRESS = "0xA601877977340862Ca67f816eb079958E5bd0BA3"
MIN_BALANCE = 25_000_000  # Minimum BOTCOIN to mine


class BotcoinMiner:
    def __init__(self):
        self.miner_address: Optional[str] = None
        self.token: Optional[str] = None
        
        # Stats tracking
        self.stats = {
            "solves": 0,
            "fails": 0,
            "total_attempts": 0,
            "start_time": None,
            "last_solve_time": None,
            "epochs": {}  # {"epoch_id": {"solves": 0, "fails": 0, "credits": 0}}
        }
        self.stats_file = "mining_stats.json"
        self._load_stats()
        
        # Use cloudscraper if available and enabled (bypasses Cloudflare)
        if USE_CLOUDSCRAPER and HAS_CLOUDSCRAPER:
            print("[INIT] Using cloudscraper for Cloudflare bypass")
            self.session = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'darwin',
                    'desktop': True
                }
            )
        else:
            # Fallback to regular requests with browser headers
            print("[INIT] Using requests with browser headers")
            self.session = requests.Session()
            self.session.headers.update({
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            })
        
    def _load_stats(self):
        """Load stats from file if exists."""
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, 'r') as f:
                    self.stats = json.load(f)
        except:
            pass
    
    def _save_stats(self):
        """Save stats to file."""
        try:
            with open(self.stats_file, 'w') as f:
                json.dump(self.stats, f, indent=2)
        except:
            pass
        
    def log(self, msg: str):
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        log_line = f"[{timestamp}] {msg}"
        print(log_line)
        
        # Also write to log file
        try:
            with open("miner.log", 'a') as f:
                f.write(log_line + "\n")
        except:
            pass
    
    # ==================== BANKR API ====================
    
    def bankr_get(self, endpoint: str) -> Dict:
        """Call Bankr API GET endpoint."""
        resp = self.session.get(
            f"https://api.bankr.bot{endpoint}",
            headers={"X-API-Key": BANKR_API_KEY}
        )
        return resp.json()
    
    def bankr_post(self, endpoint: str, data: Dict) -> Dict:
        """Call Bankr API POST endpoint."""
        resp = self.session.post(
            f"https://api.bankr.bot{endpoint}",
            headers={
                "X-API-Key": BANKR_API_KEY,
                "Content-Type": "application/json"
            },
            json=data
        )
        return resp.json()
    
    def bankr_prompt(self, prompt: str, timeout: int = 60) -> str:
        """Send natural language prompt to Bankr and wait for response."""
        job = self.bankr_post("/agent/prompt", {"prompt": prompt})
        job_id = job.get("jobId")
        if not job_id:
            raise Exception(f"Failed to submit prompt: {job}")
        
        # Poll for completion
        start = time.time()
        while time.time() - start < timeout:
            result = self.bankr_get(f"/agent/job/{job_id}")
            if result.get("status") == "completed":
                return result.get("response", "")
            elif result.get("status") in ("failed", "cancelled"):
                raise Exception(f"Job failed: {result}")
            time.sleep(2)
        raise Exception(f"Timeout waiting for Bankr response")
    
    def bankr_sign(self, message: str) -> str:
        """Sign a message via Bankr."""
        result = self.bankr_post("/agent/sign", {
            "signatureType": "personal_sign",
            "message": message
        })
        sig = result.get("signature")
        if not sig:
            raise Exception(f"Failed to sign: {result}")
        return sig
    
    def bankr_submit_tx(self, tx: Dict, description: str = "") -> Dict:
        """Submit a raw transaction via Bankr."""
        return self.bankr_post("/agent/submit", {
            "transaction": tx,
            "description": description,
            "waitForConfirmation": True
        })
    
    # ==================== WALLET & BALANCE ====================
    
    def get_miner_address(self) -> str:
        """Get the miner's EVM wallet address."""
        result = self.bankr_get("/agent/me")
        if not result.get("success"):
            raise Exception(f"Failed to get wallet: {result}")
        
        for wallet in result.get("wallets", []):
            if wallet.get("chain") == "evm":
                self.miner_address = wallet.get("address")
                self.log(f"Miner address: {self.miner_address}")
                return self.miner_address
        raise Exception("No EVM wallet found")
    
    def check_balance(self) -> Dict[str, float]:
        """Check ETH and BOTCOIN balances via Bankr."""
        self.log("Checking balances...")
        
        # Get ETH balance
        eth_response = self.bankr_prompt("what is my ETH balance on base?")
        self.log(f"ETH: {eth_response}")
        
        # Note: We don't need to parse BOTCOIN balance - coordinator checks on-chain
        return {"botcoin": 100_000_000, "eth_response": eth_response}  # Dummy, coordinator will verify
    
    def ensure_balance(self) -> bool:
        """Skip balance check - coordinator verifies on-chain."""
        self.log(f"Model: {MODEL}")
        return True
    
    # ==================== AUTH ====================
    
    def auth(self) -> str:
        """Authenticate with coordinator and get bearer token.
        Handles errors per spec:
        - nonce: 429/5xx retry, other 4xx fail
        - verify: 429 retry w/ backoff (max 3), then sleep 60-120s, 5xx retry, 401 re-sign, 403 stop
        """
        self.log("Authenticating with coordinator...")
        
        # Track verify attempts for backoff
        verify_attempts = 0
        max_verify_attempts = 3
        
        while True:
            # Step 1: Get nonce
            nonce_resp = self._auth_get_nonce()
            if not nonce_resp:
                raise Exception("Failed to get nonce after retries")
            
            nonce_data = nonce_resp.json()
            message = nonce_data.get("message", "")
            if not message:
                raise Exception(f"Failed to get nonce: {nonce_data}")
            
            # Step 2: Sign message
            signature = self.bankr_sign(message)
            
            # Step 3: Verify with retry logic
            verify_attempts += 1
            token = self._auth_verify(message, signature, verify_attempts, max_verify_attempts)
            
            if token:
                self.token = token
                self.log("Authenticated successfully")
                return self.token
            
            # If verify failed with retryable error and we haven't exhausted attempts, loop will continue
            # If 403, it will raise exception and stop
    
    def _auth_get_nonce(self) -> Optional[requests.Response]:
        """Get nonce with retry on 429/5xx."""
        backoff = [2, 4, 8]
        
        for attempt in range(len(backoff) + 1):
            resp = self.session.post(
                f"{COORDINATOR_URL}/v1/auth/nonce",
                json={"miner": self.miner_address}
            )
            
            if resp.status_code == 200:
                return resp
            
            if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                if attempt < len(backoff):
                    wait = backoff[attempt]
                    self.log(f"Nonce {resp.status_code}, retry in {wait}s")
                    time.sleep(wait)
                    continue
            
            # Other 4xx - fail
            self.log(f"Nonce error: {resp.status_code} {resp.text[:200]}")
            raise Exception(f"Nonce failed: {resp.status_code}")
        
        return None
    
    def _auth_verify(self, message: str, signature: str, attempt_num: int, max_attempts: int) -> Optional[str]:
        """Verify and get token with full error handling."""
        backoff = [2, 4, 8]
        
        for attempt in range(len(backoff) + 1):
            resp = self.session.post(
                f"{COORDINATOR_URL}/v1/auth/verify",
                json={
                    "miner": self.miner_address,
                    "message": message,
                    "signature": signature
                }
            )
            
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("token")
                if token:
                    return token
                raise Exception(f"Verify succeeded but no token: {data}")
            
            # Handle specific error codes
            if resp.status_code == 429:
                if attempt < len(backoff):
                    wait = backoff[attempt]
                    self.log(f"Verify 429, retry in {wait}s (attempt {attempt + 1})")
                    time.sleep(wait)
                    continue
                elif attempt_num < max_attempts:
                    # Max 429s - sleep 60-120s then retry whole auth
                    sleep_time = random.randint(60, 120)
                    self.log(f"Verify still 429 after retries, sleeping {sleep_time}s and retrying auth")
                    time.sleep(sleep_time)
                    return None  # Trigger retry of full auth
            
            if resp.status_code == 401:
                # Token expired - need fresh nonce and re-sign
                self.log("Verify 401, re-signing...")
                return None  # Will trigger new nonce in main auth loop
            
            if resp.status_code == 403:
                self.log(f"Verify 403 - insufficient balance: {resp.text[:200]}")
                raise Exception(f"Insufficient BOTCOIN balance to mine")
            
            if 500 <= resp.status_code < 600:
                if attempt < len(backoff):
                    wait = backoff[attempt]
                    self.log(f"Verify {resp.status_code}, retry in {wait}s")
                    time.sleep(wait)
                    continue
            
            # Other errors
            self.log(f"Verify error: {resp.status_code} {resp.text[:200]}")
            raise Exception(f"Verify failed: {resp.status_code}")
        
        return None
    
    # ==================== CHALLENGE ====================
    
    def get_challenge(self) -> Dict:
        """Request a new challenge.
        Handles errors per spec:
        - 429/5xx: retry
        - 401: re-auth then retry
        - 403: stop (insufficient balance)
        """
        import secrets
        backoff = [2, 4, 8, 16, 30]
        
        for attempt in range(len(backoff) + 1):
            nonce = secrets.token_hex(16)
            
            resp = self.session.get(
                f"{COORDINATOR_URL}/v1/challenge",
                params={"miner": self.miner_address, "nonce": nonce},
                headers={"Authorization": f"Bearer {self.token}"}
            )
            
            self.log(f"Challenge response status: {resp.status_code}")
            
            if resp.status_code == 200:
                break
            
            if resp.status_code == 401:
                try:
                    error_data = resp.json()
                    if error_data.get("reason") == "token_expired":
                        self.log("Challenge 401, re-authenticating...")
                        self.auth()
                        continue  # Retry with new token
                except:
                    pass
            
            if resp.status_code == 403:
                self.log(f"Challenge 403 - insufficient balance: {resp.text[:200]}")
                raise Exception(f"Insufficient BOTCOIN balance to mine")
            
            if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                if attempt < len(backoff):
                    wait = backoff[attempt]
                    self.log(f"Challenge {resp.status_code}, retry in {wait}s")
                    time.sleep(wait)
                    continue
            
            # Other errors
            self.log(f"Challenge error: {resp.status_code} {resp.text[:200]}")
            raise Exception(f"Challenge failed: {resp.status_code}")
        
        # Handle empty response
        if not resp.text.strip():
            raise Exception("Empty response from challenge endpoint")
        
        try:
            challenge = resp.json()
        except json.JSONDecodeError as e:
            self.log(f"Challenge response was not JSON: {resp.text[:500]}")
            raise Exception(f"Failed to parse challenge response as JSON: {e}")
        
        if "error" in challenge:
            raise Exception(f"Challenge error: {challenge}")
        
        challenge["_nonce"] = nonce
        epoch_id = challenge.get('epochId')
        credits = challenge.get('creditsPerSolve', 1)
        self.log(f"Got challenge epoch {epoch_id} | Credits per solve: {credits} ⛏️")
        return challenge
    
    # ==================== SOLVE ====================
    
    def solve(self, challenge: Dict, previous_artifact: str = None, failed_constraints: list = None, model: str = None, log_file: str = None) -> str:
        """Solve the challenge using LLM.
        
        Args:
            challenge: Challenge dict
            previous_artifact: Previous failed artifact (for retry context)
            failed_constraints: List of failed constraint indices
            model: Model to use (defaults to MODEL)
            log_file: If provided, stream output to this file
        """
        # Use provided model or default to MODEL
        model = model or MODEL
        
        doc = challenge.get("doc", "")
        questions = challenge.get("questions", [])
        constraints = challenge.get("constraints", [])
        companies = challenge.get("companies", [])
        
        # Base prompt
        prompt = f"""You are solving a BOTCOIN mining challenge. This requires precise multi-hop reasoning and constraint satisfaction.

DOCUMENT:
{doc}

VALID COMPANY NAMES (answers must match exactly):
{json.dumps(companies, indent=2)}

QUESTIONS TO ANSWER:
{json.dumps(questions, indent=2)}

CONSTRAINTS (your artifact must satisfy ALL of these):
{json.dumps(constraints, indent=2)}"""
        
        # Add self-correction feedback if this is a retry
        if previous_artifact and failed_constraints:
            prompt += f"""

=== SELF-CORCTION MODE ===
Your previous artifact FAILED. Here's what went wrong:

Previous artifact: "{previous_artifact}"

Failed constraints: {failed_constraints}
(Constraint indices are 0-based: 0 = first constraint, 1 = second, etc.)

You must fix these specific issues. Re-analyze the document and constraints.
Pay extra attention to:
- Exact word count requirements
- Required words/phrases that must be included
- Forbidden letters that must NOT appear
- Acrostic requirements (first letters of first N words)
- Arithmetic calculations (primes, equations)

COMMON MISTAKES TO AVOID:
1. Wrong word count - count EXACTLY
2. Missing required words - check spelling exactly
3. Forbidden letters - scan every word carefully
4. Wrong acrostic - verify first letters match target
5. Arithmetic errors - recalculate primes and equations

Now construct a NEW artifact that fixes these issues."""
        else:
            prompt += """

INSTRUCTIONS - Follow these steps exactly:

STEP 1: ANSWER EACH QUESTION
For each question, identify the exact company name from the document. Output your answers as:
Q1: [exact company name]
Q2: [exact company name]
...

STEP 2: EXTRACT REQUIRED VALUES
From your answers, extract:
- Required city/country/names
- Employee counts for calculations
- Revenue figures for equations
- Any other values needed for constraints

STEP 3: CALCULATE PRECISE VALUES
For arithmetic constraints (primes, equations), show your work:
- nextPrime(X): calculate step by step
- A+B=C: show each value

STEP 4: CONSTRUCT THE ARTIFACT
Build a single-line artifact that satisfies ALL constraints. Verify:
- Word count is EXACT
- All required words are included
- No forbidden letters appear
- Acrostic spells the target"""
        
        prompt += """

STEP 5: OUTPUT ONLY THE ARTIFACT
Your final output must be EXACTLY ONE LINE - the artifact string.
No explanation. No preamble. No JSON. Just the artifact.

ARTIFACT:"""

        self.log(f"Solving with {model}...{'(RETRY)' if previous_artifact else ''}")
        
        # Call Venice AI API with streaming to avoid server timeout
        resp = requests.post(
            f"{VENICE_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {VENICE_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,  # Deterministic output
                "max_tokens": MAX_TOKENS if MAX_TOKENS > 0 else 64000,  # Venice requires number
                "stream": True,  # Enable streaming to avoid server timeout
            },
            stream=True,  # Requests library streaming
            timeout=None,  # No client timeout
        )
        
        # Check for errors in stream
        if resp.status_code != 200:
            error_text = resp.text
            try:
                error_json = resp.json()
                error_msg = error_json.get("error", error_json.get("message", error_text))
            except:
                error_msg = error_text
            raise Exception(f"Venice AI error ({resp.status_code}): {error_msg}")
        
        # Collect streaming response
        self.log("Streaming response from Venice AI...")
        artifact_chunks = []
        reasoning_chunks = []
        finish_reason = None
        prompt_tokens = 0
        completion_tokens = 0
        chunk_count = 0
        
        # Open log file if provided
        log_fp = None
        if log_file:
            log_fp = open(log_file, "a")
            log_fp.write(f"\n{'='*60}\n")
            log_fp.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] FAILED CHALLENGE\n")
            log_fp.write(f"Model: {model}\n")
            log_fp.write(f"Constraints: {challenge.get('constraints', [])}\n")
            log_fp.write(f"{'='*60}\n\n")
        
        for line in resp.iter_lines():
            if not line:
                continue
            line = line.decode('utf-8')
            if not line.startswith('data: '):
                continue
            data = line[6:]  # Remove 'data: ' prefix
            if data == '[DONE]':
                break
            try:
                chunk = json.loads(data)
                delta = chunk.get('choices', [{}])[0].get('delta', {})
                
                # Collect content (handle None values)
                content = delta.get('content')
                if content:
                    artifact_chunks.append(content)
                    print(content, end='', flush=True)
                    if log_fp:
                        log_fp.write(content)
                    chunk_count += 1
                
                reasoning = delta.get('reasoning_content')
                if reasoning:
                    reasoning_chunks.append(reasoning)
                    print(f"\033[90m{reasoning}\033[0m", end='', flush=True)
                    if log_fp:
                        log_fp.write(f"[REASONING] {reasoning}")
                    chunk_count += 1
                
                # Track finish reason
                if chunk.get('choices', [{}])[0].get('finish_reason'):
                    finish_reason = chunk['choices'][0]['finish_reason']
                
                # Track usage if provided
                usage = chunk.get('usage', {})
                if usage:
                    prompt_tokens = usage.get('prompt_tokens', prompt_tokens)
                    completion_tokens = usage.get('completion_tokens', completion_tokens)
                    
            except json.JSONDecodeError:
                continue
        
        # Newline after streaming output
        if chunk_count > 0:
            print()
        
        self.log(f"Received {chunk_count} chunks")
        
        # Combine chunks
        artifact = ''.join(artifact_chunks).strip()
        reasoning = ''.join(reasoning_chunks).strip()
        
        # Use reasoning_content if content is empty (DeepSeek reasoning mode)
        if not artifact and reasoning:
            self.log("Found output in reasoning_content (DeepSeek reasoning mode)")
            artifact = reasoning
        
        # Close log file and write artifact
        if log_fp:
            log_fp.write(f"\n\nARTIFACT: {artifact}\n")
            log_fp.write(f"[TOKENS: {prompt_tokens}+{completion_tokens}]\n")
            log_fp.close()
        
        # Log token usage
        if prompt_tokens or completion_tokens:
            self.log(f"Token usage: {prompt_tokens} prompt + {completion_tokens} completion = {prompt_tokens + completion_tokens} total")
        
        # Check for issues
        if finish_reason and finish_reason not in ("stop", "length"):
            self.log(f"Warning: Venice AI finish_reason: {finish_reason}")
        
        if finish_reason == "length":
            self.log("Warning: Hit max_tokens limit")
        
        if not artifact:
            raise Exception(f"Empty artifact from Venice AI")
        
        if not artifact:
            raise Exception(f"Empty artifact from Venice AI: {result}")
        
        # Extract just the last line if model included reasoning
        lines = [l.strip() for l in artifact.split('\n') if l.strip()]
        if lines:
            for line in reversed(lines):
                if not any(line.upper().startswith(prefix) for prefix in 
                          ['Q1:', 'Q2:', 'Q3:', 'Q4:', 'Q5:', 'Q6:', 'Q7:', 'Q8:', 'Q9:', 'Q10:',
                           'STEP', 'ANSWER', 'ARTIFACT:', 'NOTE', 'VERIFY', 'CONSTRAINT',
                           'PREVIOUS', 'FAILED', 'SELF-CORRECTION']):
                    artifact = line
                    break
        
        self.log(f"Artifact ({len(artifact.split())} words): {artifact[:100]}...")
        return artifact
    
    # ==================== SUBMIT ====================
    
    def submit(self, challenge: Dict, artifact: str) -> Dict:
        """Submit the solution to coordinator.
        Handles errors per spec:
        - 429/5xx: retry
        - 401: re-auth, retry same solve
        - 404: stale challenge; return error to trigger new challenge
        - 200 pass:false: solver failed constraints (not transport error)
        """
        backoff = [2, 4, 8]
        
        for attempt in range(len(backoff) + 1):
            resp = self.session.post(
                f"{COORDINATOR_URL}/v1/submit",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json"
                },
                json={
                    "miner": self.miner_address,
                    "challengeId": challenge.get("challengeId"),
                    "artifact": artifact,
                    "nonce": challenge.get("_nonce")
                }
            )
            
            # Handle 401 token expired
            if resp.status_code == 401:
                try:
                    error_data = resp.json()
                    if error_data.get("reason") == "token_expired":
                        self.log("Submit 401, re-authenticating...")
                        self.auth()
                        continue  # Retry with new token
                except:
                    pass
            
            # Handle 404 - stale challenge
            if resp.status_code == 404:
                self.log(f"Submit 404 - stale challenge: {resp.text[:200]}")
                return {"error": "stale_challenge", "pass": False}
            
            # Handle 429/5xx with backoff
            if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                if attempt < len(backoff):
                    wait = backoff[attempt]
                    self.log(f"Submit {resp.status_code}, retry in {wait}s")
                    time.sleep(wait)
                    continue
            
            # Non-retryable error (not 200)
            if resp.status_code != 200:
                self.log(f"Submit error: {resp.status_code} {resp.text[:200]}")
                raise Exception(f"Submit failed: {resp.status_code}")
            
            # Success - got 200 response
            break
        
        # Handle empty response
        if not resp.text.strip():
            raise Exception("Empty response from submit endpoint")
        
        try:
            result = resp.json()
        except json.JSONDecodeError as e:
            self.log(f"Submit response was not JSON: {resp.text[:500]}")
            raise Exception(f"Failed to parse submit response as JSON: {e}")
        
        if result.get("pass"):
            self.log(f"✓ PASSED! Credits earned.")
        else:
            failed = result.get("failedConstraintIndices", [])
            self.log(f"✗ FAILED. Constraints: {failed}")
        
        return result
    
    # ==================== ON-CHAIN ====================
    
    def post_receipt(self, submit_result: Dict) -> Dict:
        """Post the mining receipt on-chain."""
        tx = submit_result.get("transaction")
        if not tx:
            raise Exception("No transaction in submit result")
        
        self.log("Posting receipt on-chain...")
        result = self.bankr_submit_tx(tx, "Post BOTCOIN mining receipt")
        
        if result.get("success"):
            self.log(f"✓ TX confirmed: {result.get('transactionHash', '')[:16]}...")
        else:
            self.log(f"✗ TX failed: {result}")
        
        return result
    
    # ==================== MAIN LOOP ====================
    
    def show_stats(self):
        """Display mining statistics."""
        elapsed = time.time() - self.stats.get("start_time", time.time())
        hours = elapsed / 3600
        
        print("\n" + "=" * 50)
        print("📊 MINING STATISTICS")
        print("=" * 50)
        print(f"Runtime: {hours:.2f} hours")
        print(f"Total Solves: {self.stats['solves']}")
        print(f"Total Failures: {self.stats['fails']}")
        
        if self.stats['solves'] + self.stats['fails'] > 0:
            success_rate = self.stats['solves'] / (self.stats['solves'] + self.stats['fails']) * 100
            print(f"Success Rate: {success_rate:.1f}%")
        
        if hours > 0 and self.stats['solves'] > 0:
            solves_per_hour = self.stats['solves'] / hours
            print(f"Solves/Hour: {solves_per_hour:.2f}")
        
        print(f"Last Solve: {self.stats.get('last_solve_time', 'N/A')}")
        
        # Epoch-level stats
        if self.stats.get("epochs"):
            print("\n📅 EPOCH STATS")
            print("-" * 30)
            total_credits = 0
            for epoch_id in sorted(self.stats["epochs"].keys(), key=lambda x: int(x) if x.isdigit() else 0):
                e = self.stats["epochs"][epoch_id]
                print(f"Epoch {epoch_id}: {e['solves']} solves, {e['fails']} fails, {e['credits']} credits")
                total_credits += e['credits']
            print(f"Total Credits: {total_credits}")
        
        print("=" * 50 + "\n")
    
    def mine_one(self) -> bool:
        """Run one mining cycle: solve → submit → if pass:false, get new challenge.
        Returns True if successful."""
        
        # Get challenge
        challenge = self.get_challenge()
        
        # Solve with logging to failures.log
        self.log(f"Solving with {MODEL}")
        artifact = self.solve(challenge, model=MODEL, log_file="failures.log")
        result = self.submit(challenge, artifact)
        
        # Get epoch info
        epoch_id = challenge.get("epochId", "unknown")
        credits_earned = result.get("creditsPerSolve", 0)
        
        # Initialize epoch if needed
        if epoch_id not in self.stats["epochs"]:
            self.stats["epochs"][epoch_id] = {"solves": 0, "fails": 0, "credits": 0}
        
        if result.get("pass"):
            self.post_receipt(result)
            self.stats["solves"] += 1
            self.stats["epochs"][epoch_id]["solves"] += 1
            self.stats["epochs"][epoch_id]["credits"] += credits_earned
            self.stats["last_solve_time"] = time.strftime('%Y-%m-%d %H:%M:%S')
            self._save_stats()
            return True
        
        # Submission failed (pass: false) - get new challenge
        failed_constraints = result.get("failedConstraintIndices", [])
        self.log(f"Failed. Constraints: {failed_constraints}")
        self.stats["fails"] += 1
        self.stats["epochs"][epoch_id]["fails"] += 1
        self._save_stats()
        return False
    
    def run(self):
        """Main mining loop."""
        self.log("=" * 50)
        self.log("BOTCOIN Miner Starting")
        self.log("=" * 50)
        
        # Validate config
        if not BANKR_API_KEY:
            raise Exception("BANKR_API_KEY not set")
        if not VENICE_API_KEY:
            raise Exception("VENICE_API_KEY not set")
        
        # Get miner address
        self.get_miner_address()
        
        # Check balance
        if not self.ensure_balance():
            self.log("ERROR: Insufficient BOTCOIN balance to mine")
            self.log(f"Need {MIN_BALANCE:,} BOTCOIN. Fund wallet and restart.")
            return
        
        # Auth
        self.auth()
        
        # Mining loop
        self.log("Starting mining loop...")
        self.stats["start_time"] = time.time()
        self._save_stats()
        
        last_stats_time = time.time()
        
        while True:
            try:
                self.stats["total_attempts"] += 1
                success = self.mine_one()
                
                # Show stats every 10 minutes
                if time.time() - last_stats_time > 600:
                    self.show_stats()
                    last_stats_time = time.time()
                
                # Re-auth if needed (token expires)
                # Tokens last ~10 minutes, re-auth every 8 minutes
                time.sleep(5)
                
            except KeyboardInterrupt:
                self.log("Stopping miner...")
                self.show_stats()
                break
            except Exception as e:
                self.log(f"Error in loop: {e}")
                time.sleep(10)


if __name__ == "__main__":
    miner = BotcoinMiner()
    miner.run()
