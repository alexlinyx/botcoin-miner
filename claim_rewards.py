#!/usr/bin/env python3
"""
BOTCOIN Auto-Claim - Claims rewards after epoch ends and reports stats.
"""

import os
import json
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Configuration
COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "https://coordinator.agentmoney.net")
BANKR_API_KEY = os.environ.get("BANKR_API_KEY")
MINER_ADDRESS = os.environ.get("MINER_ADDRESS")

BOTCOIN_ADDRESS = "0xA601877977340862Ca67f816eb079958E5bd0BA3"

# Use cloudscraper for Cloudflare bypass
try:
    import cloudscraper
    session = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'darwin', 'desktop': True})
except:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    })


def log(msg: str):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}")


def get_epoch_info():
    """Get current epoch information."""
    resp = session.get(f"{COORDINATOR_URL}/v1/epoch")
    return resp.json()


def get_claimable_epochs():
    """Find epochs that can be claimed."""
    epoch_info = get_epoch_info()
    current_epoch = int(epoch_info.get("epochId", 0))
    prev_epoch = int(epoch_info.get("prevEpochId", 0))
    
    if not prev_epoch:
        return []
    
    claimable = []
    
    # Check last 5 epochs
    for epoch_id in range(max(1, prev_epoch - 4), prev_epoch + 1):
        try:
            resp = session.get(
                f"{COORDINATOR_URL}/v1/claim-calldata",
                params={"epochs": epoch_id}
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("transaction"):
                    claimable.append({
                        "epoch": epoch_id,
                        "calldata": data
                    })
        except Exception as e:
            log(f"Error checking epoch {epoch_id}: {e}")
    
    return claimable


def submit_claim(claim_data):
    """Submit claim transaction via Bankr."""
    tx = claim_data.get("calldata", {}).get("transaction")
    if not tx:
        return {"success": False, "error": "No transaction data"}
    
    resp = requests.post(
        "https://api.bankr.bot/agent/submit",
        headers={
            "X-API-Key": BANKR_API_KEY,
            "Content-Type": "application/json"
        },
        json={
            "transaction": tx,
            "description": f"Claim BOTCOIN rewards for epoch {claim_data['epoch']}",
            "waitForConfirmation": True
        }
    )
    
    result = resp.json()
    return result


def get_mining_stats():
    """Get mining stats from file."""
    try:
        with open("mining_stats.json", "r") as f:
            return json.load(f)
    except:
        return {"solves": 0, "fails": 0}


def main():
    log("=" * 50)
    log("BOTCOIN Auto-Claim Check")
    log("=" * 50)
    
    # Get epoch info
    epoch_info = get_epoch_info()
    current_epoch = epoch_info.get("epochId")
    log(f"Current epoch: {current_epoch}")
    
    # Check for claimable epochs
    claimable = get_claimable_epochs()
    
    if not claimable:
        log("No claimable epochs found")
    else:
        log(f"Found {len(claimable)} claimable epoch(s)")
        
        for claim_data in claimable:
            epoch_id = claim_data["epoch"]
            log(f"Claiming epoch {epoch_id}...")
            
            result = submit_claim(claim_data)
            
            if result.get("success"):
                tx_hash = result.get("transactionHash", "")
                log(f"✓ Claimed epoch {epoch_id}: {tx_hash}")
            else:
                error = result.get("error", result.get("message", "Unknown error"))
                log(f"✗ Failed to claim epoch {epoch_id}: {error}")
    
    # Get stats
    stats = get_mining_stats()
    solves = stats.get("solves", 0)
    fails = stats.get("fails", 0)
    total = solves + fails
    success_rate = (solves / total * 100) if total > 0 else 0
    
    log("")
    log("=" * 50)
    log("MINING STATS")
    log("=" * 50)
    log(f"Total solves: {solves}")
    log(f"Total fails: {fails}")
    log(f"Success rate: {success_rate:.1f}%")
    log(f"Credits earned: ~{solves * 3}")  # Tier 3
    
    # Return summary for reporting
    return {
        "epoch": current_epoch,
        "claimable_count": len(claimable),
        "solves": solves,
        "fails": fails,
        "success_rate": success_rate,
        "credits": solves * 3
    }


if __name__ == "__main__":
    result = main()
    
    # Print summary as JSON for easy parsing
    print("\n" + "=" * 50)
    print("SUMMARY_JSON:")
    print(json.dumps(result, indent=2))
