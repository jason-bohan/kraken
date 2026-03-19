# Data Sources for Crypto Trading Bots

## 1. Fear & Greed Index API
**Purpose:** Crypto market sentiment indicator  
**Source:** https://alternative.me/crypto/fear-and-greed-index/  
**Cost:** Free  

**Why it matters:**  
Using sentiment helps bots avoid buying during extreme panic.  
A simple rule such as:

- **Avoid buys when Fear Index < 25**

…would prevent many falling-knife entries.

---

## 2. CoinGecko or CoinMarketCap API
**Purpose:** Broad crypto market data  
**Examples of available data:**
- Trending coins  
- Market cap rankings  
- Sector/industry performance  

**Cost:** Free tiers available  

**Why it matters:**  
Improves momentum scanners by giving them better candidate coins and richer market context.

---

## 3. On‑Chain Data (Nice-to-Have)
**Purpose:** Advanced blockchain analytics  
**Examples of signals:**
- Whale wallet movements  
- Exchange inflows/outflows  

**Possible providers:**  
- Glassnode  
- CryptoQuant  

**Cost:** Free tiers available  

**Why it matters:**  
Adds deeper insight into market behavior and can serve as a high‑value confirmation signal.

