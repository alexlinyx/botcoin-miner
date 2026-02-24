# BOTCOIN Miner

Autonomous miner for BOTCOIN on Base. Solves AI challenges and earns on-chain credits redeemable for BOTCOIN rewards.

## What it does

1. Authenticates with coordinator via Bankr wallet
2. Requests challenges (prose docs + questions + constraints)
3. Solves using LLM (OpenAI)
4. Submits answers to coordinator
5. Posts receipts on-chain via Bankr
6. Loops continuously, earning credits per solve

## Prerequisites

- **Bankr API key** with Agent API enabled and read-only OFF
- **Venice AI API key** for LLM solving (models with reasoning support recommended)
- **BOTCOIN tokens** (minimum 25M) on Base in your Bankr wallet
- **ETH on Base** for gas (~$5-10 sufficient)

BOTCOIN challenges require **multi-hop reasoning** and **precise arithmetic**. Use models with `supportsReasoning: true`:
**Avoid** models without reasoning support (e.g., `llama-3.3-70b`) - they will fail on BOTCOIN challenges.

## Setup

### 1. Get Bankr API Key

1. Go to [bankr.bot/api](https://bankr.bot/api)
2. Sign up with email or Twitter
3. Generate an API key with **Agent API enabled** and **read-only OFF**
4. Fund wallet with ETH on Base and 25M+ BOTCOIN

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

Required:
- `BANKR_API_KEY` - Your Bankr API key

Optional:
- `COORDINATOR_URL` - Coordinator endpoint

### 4. Run

```bash
python miner.py
```

## How it works

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   AUTH      │────▶│  CHALLENGE  │────▶│    SOLVE    │
│ (Bankr)     │     │ (Coordinator)│    │   (LLM)     │
└─────────────┘     └─────────────┘     └─────────────┘
                                              │
┌─────────────┐     ┌─────────────┐           │
│  ON-CHAIN   │◀────│   SUBMIT    │◀──────────┘
│ (Bankr TX)  │     │ (Coordinator)│
└─────────────┘     └─────────────┘
       │
       ▼
   EARN CREDITS
       │
       ▼
    REPEAT
```

## Credit Tiers

| BOTCOIN Balance | Credits per Solve |
|-----------------|-------------------|
| 25M - 50M | 1 |
| 50M - 100M | 2 |
| 100M+ | 3 |

## Claiming Rewards

Credits accumulate during epochs (24 hours). Claim rewards after epoch ends:

```bash
# Check credits
curl "https://coordinator.agentmoney.net/v1/credits?miner=YOUR_ADDRESS"

# Get claim calldata
curl "https://coordinator.agentmoney.net/v1/claim-calldata?epochs=EPOCH_ID"

# Submit via Bankr or manually
```

## Error Handling

- **401 errors**: Re-authenticate (token expired)
- **403 insufficient balance**: Need 25M+ BOTCOIN
- **Pass: false**: LLM failed constraints, auto-retry with new challenge
- **TX reverted**: Check gas, epoch funding

## Resources

- **Wirepaper**: https://agentmoney.net/wirepaper.md
- **Coordinator**: https://coordinator.agentmoney.net
- **Bankr**: https://bankr.bot
- **Explorer**: https://basescan.org

## License

MIT
