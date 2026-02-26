#!/usr/bin/env python3
"""
Debug - see what's being sent
"""
import os, time, requests, hmac, base64, hashlib, urllib.parse

api_key = os.getenv("KRAKEN_API_KEY")
api_secret = os.getenv("KRAKEN_API_SECRET")

nonce = int(time.time() * 1000)

# Try with BOTH cost and volume
data = {
    "nonce": nonce,
    "ordertype": "market",
    "type": "buy",
    "pair": "SOLUSD",
    "cost": "5.00",  # $5 minimum
}

postdata = urllib.parse.urlencode(data)
sha256_hash = hashlib.sha256(str(nonce) + postdata).digest()
uri_path = "/0/private/AddOrder"
message = uri_path + sha256_hash
signature = hmac.new(base64.b64decode(api_secret), message.encode(), hashlib.sha512).digest()
headers = {
    "API-Key": api_key,
    "API-Sign": base64.b64encode(signature).decode(),
    "Content-Type": "application/json"
}

res = requests.post("https://api.kraken.com" + uri_path, json=data, headers=headers, timeout=10)
print("Response:", res.json())
