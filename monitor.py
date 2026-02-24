#!/usr/bin/env python3
"""
BOTCOIN Mining Monitor - Track mining progress, credits, and rewards.
"""

import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Configuration
COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "https://coordinator.agentmoney.net")
BANKR_API_KEY = os.environ.get("BANKR_API_KEY")

BOTCOIN_ADDRESS = "0xA601877977340862Ca67f816eb079958E5bd0BA3"

# Try to import cloudscraper for Cloudflare bypass
try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

# Use cloudscraper if available
if HAS_CLOUDSCRAPER:
    session = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'darwin', 'desktop': True})
else:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    })


def get_miner_address():
    """Get the miner's EVM wallet address."""
    resp = requests.get(
        "https://api.bankr.bot/agent/me",
        headers={"X-API-Key": BANKR_API_KEY}
    )
    data = resp.json()
    for wallet in data.get("wallets", []):
        if wallet.get("chain") == "evm":
            return wallet.get("address")
    return None


def check_bankr_balance():
    """Check ETH and BOTCOIN balances via Bankr."""
    print("\n💰 WALLET BALANCES")
    print("-" * 40)
    
    # Submit balance check
    resp = requests.post(
        "https://api.bankr.bot/agent/prompt",
        headers={
            "X-API-Key": BANKR_API_KEY,
            "Content-Type": "application/json"
        },
        json={"prompt": "what are my balances on base?"}
    )
    job = resp.json()
    job_id = job.get("jobId")
    
    # Poll for result
    import time
    for _ in range(30):
        resp = requests.get(
            f"https://api.bankr.bot/agent/job/{job_id}",
            headers={"X-API-Key": BANKR_API_KEY}
        )
        result = resp.json()
        if result.get("status") == "completed":
            print(result.get("response", "No response"))
            return
        time.sleep(2)
    
    print("Timeout waiting for balance")


def check_epoch_status():
    """Check current epoch status."""
    print("\n⏱️  EPOCH STATUS")
    print("-" * 40)
    
    try:
        resp = session.get(f"{COORDINATOR_URL}/v1/epoch")
        data = resp.json()
        
        epoch_id = data.get('epochId', 'N/A')
        prev_epoch_id = data.get('prevEpochId', 'N/A')
        
        print(f"Current Epoch: {epoch_id}")
        print(f"Previous Epoch: {prev_epoch_id}")
        
        next_start = data.get('nextEpochStartTimestamp')
        if next_start:
            try:
                next_dt = datetime.fromtimestamp(int(next_start))
                print(f"Next Epoch Starts: {next_dt}")
            except (ValueError, TypeError):
                print(f"Next Epoch Start Timestamp: {next_start}")
        
        duration = data.get('epochDurationSeconds', 0)
        if duration:
            try:
                hours = int(duration) / 3600
                print(f"Epoch Duration: {hours:.1f} hours")
            except (ValueError, TypeError):
                print(f"Epoch Duration: {duration} seconds")
            
    except Exception as e:
        print(f"Error: {e}")


def check_credits(miner_address):
    """Check mining credits."""
    print("\n⛏️  MINING CREDITS")
    print("-" * 40)
    
    if not miner_address:
        print("No miner address available")
        return
    
    try:
        resp = session.get(
            f"{COORDINATOR_URL}/v1/credits",
            params={"miner": miner_address}
        )
        data = resp.json()
        
        credits = data.get("credits", [])
        if not credits:
            print("No credits earned yet")
            return
        
        total_credits = 0
        print(f"{'Epoch':<10} {'Credits':<10} {'Solves':<10}")
        print("-" * 30)
        
        for entry in credits:
            epoch = entry.get("epochId", "N/A")
            credit = entry.get("credits", 0)
            solves = entry.get("solves", 0)
            total_credits += credit
            print(f"{epoch:<10} {credit:<10} {solves:<10}")
        
        print("-" * 30)
        print(f"{'TOTAL':<10} {total_credits:<10}")
        
    except Exception as e:
        print(f"Error: {e}")


def check_claimable_epochs(miner_address):
    """Check which epochs can be claimed."""
    print("\n🎁 CLAIMABLE REWARDS")
    print("-" * 40)
    
    # Get current epoch
    try:
        resp = session.get(f"{COORDINATOR_URL}/v1/epoch")
        epoch_data = resp.json()
        
        current_epoch = epoch_data.get("epochId", 0)
        prev_epoch = epoch_data.get("prevEpochId")
        
        # Convert to int if strings
        try:
            current_epoch = int(current_epoch)
            prev_epoch = int(prev_epoch) if prev_epoch else None
        except (ValueError, TypeError):
            pass
        
        if not prev_epoch:
            print("No completed epochs yet")
            return
        
        # Check a few previous epochs
        claimable = []
        start_epoch = max(1, int(prev_epoch) - 5)
        for epoch_id in range(start_epoch, int(prev_epoch) + 1):
            try:
                resp = session.get(
                    f"{COORDINATOR_URL}/v1/claim-calldata",
                    params={"epochs": epoch_id}
                )
                if resp.status_code == 200:
                    claimable.append(epoch_id)
            except:
                pass
        
        if claimable:
            print(f"Claimable epochs: {claimable}")
            print(f"\nTo claim: python miner.py --claim {','.join(map(str, claimable))}")
        else:
            print("No claimable epochs (not funded yet or already claimed)")
            
    except Exception as e:
        print(f"Error: {e}")


def parse_logs():
    """Parse recent miner logs if available."""
    print("\n📊 RECENT ACTIVITY")
    print("-" * 40)
    
    import glob
    
    # Look for log files
    log_patterns = ["*.log", "miner_*.log", "logs/*.log"]
    log_files = []
    for pattern in log_patterns:
        log_files.extend(glob.glob(pattern))
    
    if not log_files:
        print("No log files found")
        return
    
    # Parse most recent log
    latest_log = max(log_files, key=os.path.getmtime)
    print(f"Reading: {latest_log}")
    
    try:
        with open(latest_log, 'r') as f:
            lines = f.readlines()[-50:]  # Last 50 lines
        
        solves = 0
        fails = 0
        for line in lines:
            if "PASSED" in line:
                solves += 1
            elif "FAILED" in line:
                fails += 1
        
        print(f"Solves: {solves} | Failures: {fails}")
        
        # Show last 5 lines
        print("\nLast 5 log entries:")
        for line in lines[-5:]:
            print(f"  {line.strip()}")
            
    except Exception as e:
        print(f"Error reading log: {e}")


def main():
    print("=" * 50)
    print("⛏️  BOTCOIN MINING MONITOR")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    
    if not BANKR_API_KEY:
        print("ERROR: BANKR_API_KEY not set in .env")
        return
    
    # Get miner address
    miner_address = get_miner_address()
    print(f"\n📍 Miner Address: {miner_address or 'Not found'}")
    
    # Run all checks
    check_bankr_balance()
    check_epoch_status()
    check_credits(miner_address)
    check_claimable_epochs(miner_address)
    
    print("\n" + "=" * 50)
    print("Monitor complete")


if __name__ == "__main__":
    main()
