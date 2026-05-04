import os
import sys


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
THIRD_PARTY_DIR = os.path.join(BASE_DIR, "third_party")

if sys.platform.startswith("linux") and os.path.isdir(THIRD_PARTY_DIR):
    sys.path.insert(0, THIRD_PARTY_DIR)

from src.OlympicsWeb import app


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 9000)))
