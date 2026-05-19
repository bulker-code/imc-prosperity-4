# IMC Prosperity 4

A collection of algorithmic trading strategies developed for IMC Trading's 
Prosperity 4 competition (2026), where I competed solo and finished just shy of the 
top 10% out of 19,000 participants.

The competition consisted of two phases. The first phase, rounds 1 and 2, was a qualification stage in which competing teams would need to amass 200,000 XIRECS to qualify for finals. The second phase, rounds 3, 4 and 5, were designed to separate the good from the great and determine winners from the pool of finalists. Each trading round had both an algorithmic and a manual trading challenge. The algorithmic challenges required teams to develop a python algorithm which placed buy and sell orders to extract profit over a simulated trading day. Submissions were backtested in the website before being run on the larger official dataset each round. The manual challenges were game-theory heavy, being less about finding the perfect answer and more about predicting how thousands of competitors would react to the same information. 

## Algorithmic Strategies
- Mean reversion
- Market making
## Manual Strategies
- Game Theory
- Position optimisation using Lagrange multipliers

## Structure
Each round contains a data capsule with relevant data for the trading day.
- `ROUND1/` - Round 1 trading algorithms
- `ROUND2/` - Round 2 trading algorithms
- `ROUND3/` - Round 3 trading algorithms
- 

## Technologies
- Python
- NumPy
- Pandas
