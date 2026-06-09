# Bank of Baroda — Core Simulator

This is the standalone **Bank of Baroda Core Simulator** packaged and optimized for quick deployment to **Vercel** as a Python Serverless project.

The simulator simulates bank actions (Sign in, Transfer, Add Payee, Profile Change, Account Recovery) across 10 dummy accounts and evaluates them against the **TrustIQ** continuous trust validation system.

---

## Folder Structure

```
.
├── api/                      # Vercel Serverless Functions
│   ├── accounts.py
│   ├── ai_verifier.py
│   ├── db.py
│   ├── server.py             # FastAPI App Entrypoint
│   ├── trust_client.py
│   └── _env.py
├── index.html                # Main Customer Bank UI (Static)
├── vercel.json               # Vercel routing configuration
├── requirements.txt          # Python dependencies
└── README.md
```

---



## Deployed Application

Once deployed on Vercel, the application is live at your custom Vercel domain, for example:
- **URL**: [https://bob-simulator.vercel.app/](https://bob-simulator.vercel.app/)

