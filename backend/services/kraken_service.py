import logging
import requests
import time
import hashlib
import hmac
import base64
import urllib.parse
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

API_KEY = os.getenv("KRAKEN_API_KEY")
API_SECRET = os.getenv("KRAKEN_API_SECRET")

BASE_URL = "https://api.kraken.com"
logger = logging.getLogger("kraken_service")


# ✅ FIXED (robust, no more crashes)
def get_btc_price():
    try:
        url = f"{BASE_URL}/0/public/Ticker?pair=XBTUSD"
        res = requests.get(url, timeout=8)
        res.raise_for_status()
        payload = res.json()
        if payload.get("error"):
            raise ValueError(", ".join(payload["error"]))

        pair = next(iter(payload["result"].values()))

        price = float(pair["c"][0])
        if price <= 0:
            raise ValueError("Kraken returned an invalid BTC price")
        return price

    except Exception as e:
        logger.warning("Kraken price request failed: %s", e)
        return 0.0


# ✅ SIGNATURE (unchanged, correct)
def get_kraken_signature(urlpath, data, secret):
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data['nonce']) + postdata).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


# ✅ SAFE TRADE (validate only)
def place_order():
    try:
        if not API_KEY or not API_SECRET:
            return {"error": "Kraken API credentials are not configured"}
        urlpath = "/0/private/AddOrder"
        url = BASE_URL + urlpath

        nonce = str(int(time.time() * 1000))

        data = {
            "nonce": nonce,
            "ordertype": "market",
            "type": "buy",
            "volume": "0.0001",
            "pair": "XBTUSD",
            "validate": True  # 🚨 SAFE MODE (NO REAL TRADE)
        }

        headers = {
            "API-Key": API_KEY,
            "API-Sign": get_kraken_signature(urlpath, data, API_SECRET)
        }

        res = requests.post(url, headers=headers, data=data, timeout=10)
        res.raise_for_status()
        return res.json()

    except Exception as e:
        logger.exception("Kraken order validation failed")
        return {"error": str(e)}
