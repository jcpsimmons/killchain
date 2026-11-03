"""Shim so `python harness.py` keeps working from a checkout.

Installed users get the `killchain` command instead.
"""
from killchain.harness import main

if __name__ == "__main__":
    main()
