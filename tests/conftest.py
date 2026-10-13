import os

# Most tests drive the app directly. Sign-in is switched on per test in test_auth.py.
os.environ.setdefault("KODA_AUTH", "off")
