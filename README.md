# IMC Prosperity 4

Algorithmic trading strategies for IMC Trading's Prosperity 4 (2026), competed solo, finished just shy of the top 10% out of 19,000 participants.

Two phases: Rounds 1–2 were qualification (amass 200,000 XIRECS to advance), Rounds 3–5 were finals. Each round paired an algorithmic challenge (submit a Python trader that quotes buy/sell orders within position limits, backtested live then run on IMC's official pipeline) with a manual challenge (game-theory puzzles about predicting the field's reaction, not finding a single "correct" answer).

## Round by Round

- **Round 1** — `INTARIAN_PEPPER_ROOT` (steady linear growth) / `ASH_COATED_OSMIUM` (mean-reverting). Bought and held pepper root; traded osmium on standard-deviation mean reversion.
- **Round 2** — Same two products, plus a market-share auction (top 50% of bidders get 25% more tradeable timestamps). Bid 1000 XIRECS on osmium, cleared the median and won the extra access.
- **Round 3** — Finals begin: `VELVETFRUIT_EXTRACT`, `HYDROGEL_PACKS`, and `VELVETFRUIT_EXTRACT_VOUCHERS` (options). Ran EMA-based mean reversion on both underlyings. Manual challenge: `invest_expand_optimiser.html`.
- **Round 4** — Manual challenge: `aether_crystal_v5.html`.
- **Round 5** — *(recap pending)*

## Repo Structure

- `Tutorial/` — practice round data and mock trader setup
- `ROUND_1/` – `ROUND_5/` — each round's algo trader(s), data analysis script, and `data_capsule_*/` price & trade history
- `datamodel.py` — shared mock of IMC's trading API (`OrderDepth`, `TradingState`, `Order`, ...) used for local testing; imported by each round's trader
