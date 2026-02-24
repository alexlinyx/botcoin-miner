#!/usr/bin/env python3
"""
BOTCOIN Miner - Mines BOTCOIN by solving AI challenges on Base.
Requires BANKR_API_KEY for wallet operations and on-chain transactions.
"""

import os
import json
import time
import hashlib
import requests
from typing import Optional, Dict, Any

# Configuration
COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "https://coordinator.agentmoney.net")
BANKR_API_KEY = os.environ.get("BANKR_API_KEY")
LLM_API_KEY = os.environ.get("LLM_API_KEY")  # OpenAI API key for solving
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o")

# Botcoin token address
BOTCOIN_ADDRESS = "0xA601877977340862Ca67f816eb079958E5bd0BA3"
MIN_BALANCE = 25_000_000  # Minimum BOTCOIN to mine


class BotcoinMiner:
    def __init__(self):
        self.miner_address: Optional[str] = None
        self.token: Optional[str] = None
        self.session = requests.Session()
        
    def log(self, msg: str):
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")
    
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
        """Check ETH and BOTCOIN balances."""
        self.log("Checking balances...")
        
        # Get ETH balance
        eth_response = self.bankr_prompt("what is my ETH balance on base?")
        self.log(f"ETH: {eth_response}")
        
        # Get BOTCOIN balance
        botcoin_response = self.bankr_prompt(f"what is my balance of token {BOTCOIN_ADDRESS} on base?")
        self.log(f"BOTCOIN: {botcoin_response}")
        
        # Parse BOTCOIN amount from response
        import re
        match = re.search(r'[\d,]+\.?\d*', botcoin_response.replace(',', ''))
        botcoin_balance = float(match.group()) if match else 0
        
        return {"botcoin": botcoin_balance, "eth_response": eth_response}
    
    def ensure_balance(self) -> bool:
        """Ensure minimum BOTCOIN balance for mining."""
        balances = self.check_balance()
        if balances["botcoin"] >= MIN_BALANCE:
            self.log(f"Balance OK: {balances['botcoin']:,.0f} BOTCOIN")
            return True
        else:
            self.log(f"Insufficient BOTCOIN: {balances['botcoin']:,.0f} < {MIN_BALANCE:,}")
            return False
    
    # ==================== AUTH ====================
    
    def auth(self) -> str:
        """Authenticate with coordinator and get bearer token."""
        self.log("Authenticating with coordinator...")
        
        # Step 1: Get nonce
        resp = self.session.post(
            f"{COORDINATOR_URL}/v1/auth/nonce",
            json={"miner": self.miner_address}
        )
        nonce_data = resp.json()
        message = nonce_data.get("message", "")
        
        if not message:
            raise Exception(f"Failed to get nonce: {nonce_data}")
        
        # Step 2: Sign message
        signature = self.bankr_sign(message)
        
        # Step 3: Verify and get token
        resp = self.session.post(
            f"{COORDINATOR_URL}/v1/auth/verify",
            json={
                "miner": self.miner_address,
                "message": message,
                "signature": signature
            }
        )
        verify_data = resp.json()
        self.token = verify_data.get("token")
        
        if not self.token:
            raise Exception(f"Failed to verify: {verify_data}")
        
        self.log(f"Authenticated. Credits per solve: {verify_data.get('creditsPerSolve', 1)}")
        return self.token
    
    # ==================== CHALLENGE ====================
    
    def get_challenge(self) -> Dict:
        """Request a new challenge."""
        import secrets
        nonce = secrets.token_hex(16)
        
        resp = self.session.get(
            f"{COORDINATOR_URL}/v1/challenge",
            params={"miner": self.miner_address, "nonce": nonce},
            headers={"Authorization": f"Bearer {self.token}"}
        )
        challenge = resp.json()
        
        if "error" in challenge:
            raise Exception(f"Challenge error: {challenge}")
        
        challenge["_nonce"] = nonce
        self.log(f"Got challenge {challenge.get('challengeId', '')[:16]}... epoch {challenge.get('epochId')}")
        return challenge
    
    # ==================== SOLVE ====================
    
    def solve(self, challenge: Dict) -> str:
        """Solve the challenge using LLM."""
        doc = challenge.get("doc", "")
        questions = challenge.get("questions", [])
        constraints = challenge.get("constraints", [])
        companies = challenge.get("companies", [])
        
        prompt = f"""You are solving a BOTCOIN mining challenge. Analyze the document and answer questions, then construct an artifact.

DOCUMENT:
{doc}

COMPANIES (valid answers must match these exactly):
{json.dumps(companies, indent=2)}

QUESTIONS:
{json.dumps(questions, indent=2)}

CONSTRAINTS (your artifact must satisfy ALL of these):
{json.dumps(constraints, indent=2)}

INSTRUCTIONS:
1. Answer each question carefully by analyzing the document
2. Construct a single-line artifact that satisfies ALL constraints
3. Your response must be EXACTLY ONE LINE - the artifact string only
4. Do NOT include any explanation, reasoning, or preamble
5. Output ONLY the artifact that satisfies all constraints

ARTIFACT:"""

        self.log("Solving challenge with LLM...")
        
        # Call OpenAI API
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1
            },
            timeout=120
        )
        
        result = resp.json()
        artifact = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        
        self.log(f"Artifact ({len(artifact.split())} words): {artifact[:100]}...")
        return artifact
    
    # ==================== SUBMIT ====================
    
    def submit(self, challenge: Dict, artifact: str) -> Dict:
        """Submit the solution to coordinator."""
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
        result = resp.json()
        
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
    
    def mine_one(self) -> bool:
        """Run one mining cycle. Returns True if successful."""
        try:
            # Get challenge
            challenge = self.get_challenge()
            
            # Solve
            artifact = self.solve(challenge)
            
            # Submit
            result = self.submit(challenge, artifact)
            
            if result.get("pass"):
                # Post on-chain
                self.post_receipt(result)
                return True
            else:
                self.log("Challenge failed, getting new one...")
                return False
                
        except Exception as e:
            self.log(f"Error in mining cycle: {e}")
            return False
    
    def run(self):
        """Main mining loop."""
        self.log("=" * 50)
        self.log("BOTCOIN Miner Starting")
        self.log("=" * 50)
        
        # Validate config
        if not BANKR_API_KEY:
            raise Exception("BANKR_API_KEY not set")
        if not LLM_API_KEY:
            raise Exception("LLM_API_KEY not set")
        
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
        solve_count = 0
        fail_count = 0
        
        while True:
            try:
                success = self.mine_one()
                if success:
                    solve_count += 1
                else:
                    fail_count += 1
                
                self.log(f"Stats: {solve_count} solved, {fail_count} failed")
                
                # Re-auth if needed (token expires)
                # Tokens last ~10 minutes, re-auth every 8 minutes
                time.sleep(5)
                
            except KeyboardInterrupt:
                self.log("Stopping miner...")
                break
            except Exception as e:
                self.log(f"Error in loop: {e}")
                time.sleep(10)


if __name__ == "__main__":
    miner = BotcoinMiner()
    miner.run()
