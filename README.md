# IMC Prosperity 4

A collection of algorithmic trading strategies developed for IMC Trading's 
Prosperity 4 competition (2026), where I competed solo and finished just shy of the 
top 10% out of 19,000 participants.

The competition consisted of two phases. The first phase, rounds 1 and 2, was a qualification stage in which competing teams would need to amass 200,000 XIRECS to qualify for finals. The second phase, rounds 3, 4 and 5, were designed to separate the good from the great and determine winners from the pool of finalists. Each trading round had both an algorithmic and a manual trading challenge.  

The algorithmic challenges required teams to develop and submit a python algorithm which placed buy and sell orders to extract profit over a simulated trading day. I also had to be mindful of product position limits, making sure not to overbuy or oversell and be penalised by disqualification of the submission. During the trading round, submissions were backtested in the IMC website, and when the round ended, they were run on a larger official data pipeline. The manual challenges were game-theory heavy, being less about finding the perfect answer and more about predicting how thousands of competitors would react to the same information. 

## Round by Round Recap
### Round 1:
This round featured two products, "INTARIAN_PEPPER_ROOT" and "ASH_COATED_OSMIUM". The intarian pepper root was a "slow-growing" root that increased price steadily over time. A viable strategy and the strategy I submitted, was to buy and hold pepper root and collect profit as its price grew linearly. The other product, ash coated osmium, was not so simple, however it did oscillated around a fairly steady mean. Thus, I implemented a strategy to target mean reversion, buying or selling when the current mid price exceeded a certain standard deviation of the mean of the previous past mid prices. 

### Round 2:
Round 2 contained the same 2 products, so the aforementioned strategies of buying and holding pepper and mean reverting osmium held up fairly well. However it introduced a third variable, a bid for extra market share. It would allow the top 50% of bidders to gain access to an extra 25% of timestamps to trade on, potentially increasing profits. The buy hold strategy would not really benefit from the extra trading timestamps as it did not increase total time, so I landed on a relatively small bid of 1000 XIRECS hoping to increase the PnL from osmium. The bid was above the median of 50 and thus I did gain access to the extra market.

### Round 3:
Finals has begun and the new main products are "VELVETFRUIT_EXTRACT" and "HYDROGEL_PACKS", with "VELVETFRUIT_EXTRACT_VOUCHERS" as options trading products. For this round, I used mean reversion with an exponential moving average for both products. 

### Round 4:


### Round 5:

## Repo Structure
Each round contains a data capsule with relevant data for the trading day, accompanied by a data_analysis file so that the data can be interpreted and strategies could be developed. It also has a mock datamodel file, allowing the python files to be run in shell. 
- `ROUND1/` - Round 1 trading algorithms and manual tools
- `ROUND2/` - Round 2 trading algorithms and manual tools
- `ROUND3/` - Round 3 trading algorithms and manual tools
- `ROUND4/` - Round 4 trading algorithms and manual toold
- `ROUND5/` - Round 5 trading algorithms and manual tools

