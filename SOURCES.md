# Sources

Fee facts in `config/fee_registry.json` come only from these pages. They were checked by hand on 16 Oct 2026; re-verify before each run and update `last_checked`.

| # | Source | Used for |
|---|---|---|
| 1 | https://groww.in/pricing/stocks | DP charges, brokerage, GST, account charges (stocks) |
| 2 | https://groww.in/help/stocks%2C-f%26o%2C-ipo-%26-mtf/sx-demat-account/how-much-does-groww-charge-for-stocks-1 | Groww Help: how much Groww charges for stocks |
| 3 | https://groww.in/p/exit-load-in-mutual-funds | Exit load on mutual funds |
| 4 | https://investor.sebi.gov.in/exit_load.html | SEBI investor education: exit load |
| 5 | https://play.google.com/store/apps/details?id=com.nextbillion.groww | Public Google Play reviews (the input data) |

The explainer attaches exactly two of sources 1–4 (the two listed for the identified fee in the registry). Links are added by code from the registry, never written by the model.

Reviews are public Google Play reviews scraped with `scripts/scrape_reviews.py`. Reviewer names are not stored.
