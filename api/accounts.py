from __future__ import annotations

from copy import deepcopy
from typing import Dict, List

# The ten controllable accounts. ``home_city`` values are chosen from the set
# TrustIQ can geo-locate, so impossible-travel works out of the box.
SEED_ACCOUNTS: List[dict] = [
    {
        "user_id": "krishna_agrawal", "name": "Krishna Agrawal",
        "account_number": "3920 1100 4521", "ifsc": "BARB0MUMBAI",
        "balance": 184500.0, "home_city": "Mumbai",
        "device_id": "krishna_pixel", "device_os": "Android 14",
        "phone": "+91 98•••• 4521",
    },
    {
        "user_id": "shubham_verma", "name": "Shubham Verma",
        "account_number": "3920 1100 7733", "ifsc": "BARB0PUNE00",
        "balance": 96250.0, "home_city": "Pune",
        "device_id": "shubham_oneplus", "device_os": "Android 13",
        "phone": "+91 91•••• 7733",
    },
    {
        "user_id": "rahul_sharma", "name": "Rahul Sharma",
        "account_number": "3920 1100 1209", "ifsc": "BARB0DELHI0",
        "balance": 421000.0, "home_city": "Delhi",
        "device_id": "rahul_iphone", "device_os": "iOS 17",
        "phone": "+91 99•••• 1209",
    },
    {
        "user_id": "neha_singh", "name": "Neha Singh",
        "account_number": "3920 1100 8841", "ifsc": "BARB0DELHI0",
        "balance": 57800.0, "home_city": "Delhi",
        "device_id": "neha_samsung", "device_os": "Android 14",
        "phone": "+91 98•••• 8841",
    },
    {
        "user_id": "ananya_iyer", "name": "Ananya Iyer",
        "account_number": "3920 1100 3360", "ifsc": "BARB0CHENNA",
        "balance": 132400.0, "home_city": "Chennai",
        "device_id": "ananya_pixel", "device_os": "Android 14",
        "phone": "+91 90•••• 3360",
    },
    {
        "user_id": "arjun_nair", "name": "Arjun Nair",
        "account_number": "3920 1100 5512", "ifsc": "BARB0BLRKAR",
        "balance": 268900.0, "home_city": "Bengaluru",
        "device_id": "arjun_iphone", "device_os": "iOS 16",
        "phone": "+91 97•••• 5512",
    },
    {
        "user_id": "priya_menon", "name": "Priya Menon",
        "account_number": "3920 1100 9087", "ifsc": "BARB0KOLKAT",
        "balance": 74300.0, "home_city": "Kolkata",
        "device_id": "priya_redmi", "device_os": "Android 13",
        "phone": "+91 96•••• 9087",
    },
    {
        "user_id": "vikram_reddy", "name": "Vikram Reddy",
        "account_number": "3920 1100 6634", "ifsc": "BARB0HYDERA",
        "balance": 351200.0, "home_city": "Hyderabad",
        "device_id": "vikram_vivo", "device_os": "Android 14",
        "phone": "+91 95•••• 6634",
    },
    {
        "user_id": "sara_khan", "name": "Sara Khan",
        "account_number": "3920 1100 2218", "ifsc": "BARB0AHMEDA",
        "balance": 48900.0, "home_city": "Ahmedabad",
        "device_id": "sara_iphone", "device_os": "iOS 17",
        "phone": "+91 94•••• 2218",
    },
    {
        "user_id": "rohit_patel", "name": "Rohit Patel",
        "account_number": "3920 1100 4476", "ifsc": "BARB0SURAT0",
        "balance": 159750.0, "home_city": "Surat",
        "device_id": "rohit_samsung", "device_os": "Android 13",
        "phone": "+91 93•••• 4476",
    },
]


def fresh_accounts() -> Dict[str, dict]:
    """Return a deep copy of the seed accounts keyed by ``user_id``.

    Returns:
        A mapping of user_id -> account dict, safe to mutate at runtime.
    """
    return {a["user_id"]: deepcopy(a) for a in SEED_ACCOUNTS}
