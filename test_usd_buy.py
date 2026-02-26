#!/usr/bin/env python3
"""
Test buy with USD - spend exactly $1 from your USD balance
"""
from kraken_connection import place_order, get_balance

bal = get_balance()
usd = float(bal.get('ZUSD', 0))
print(f"USD Balance: ${usd}")

# Buy $1 worth of SOL using market order
# For market orders with quote currency, Kraken accepts 'cost' parameter
import os, time, requests

# Get API credentials
api_key = os.getenv("KRAKEN_API_KEY")
api_secret = os.getenv("KRAKEN_API_SECRET")

if api_key and api_secret:
    nonce = int(time.time() * 1000)
    data = {
        "nonce": nonce,
        "ordertype": "market",
        "type": "buy",
        "pair": "SOLUSD",
        "cost": "1.00"  # Spend exactly $1
    }
    
    headers = {
        "API-Key": api_key,
        "API-Sign": "",
        "Content-Type": "application/json"
    }
    
    # Build signature
    postdata = urllib.parse.urlencode(data)
    nonce_postdata = str(nonce) + postdata
    sha256_hash = hashlib.sha256(nonce_postdata.encode()).digest()
    uri_path = "/0/private/AddOrder"
    message = uri_path + sha256_hash
    
    import hmac, base64
    secret_decoded = base64.b64decode(api_secret)
    signature = hmac.new(secret_decoded, message.encode(), hashlib.sha512).digest()
    headers["API-Sign"] = base64.b64encode(signature).decode()
    
    try:
        res = requests.post("https://api.kraken.com" + uri_path, json=data, headers=headers, timeout=10)
        print("Response:", res.json())
    except Exception as e:
        print("Error:", e)
else:
    print("No API keys found")