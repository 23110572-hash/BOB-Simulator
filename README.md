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

## Deployment to Vercel

1. **Push to GitHub**: Make sure this repository is pushed to your GitHub.
2. **Import to Vercel**: Create a new project in Vercel and import this repository.
3. **Configure Environment Variables**:
   In your Vercel project's settings, define the following environment variables:

   | Variable | Description |
   |----------|-------------|
   | `DATABASE_URL` | **[Required]** The Neon PostgreSQL DSN connection URL. The app will automatically seed the database tables on startup. |
   | `TRUSTIQ_URL` | **[Required]** The API URL of your hosted TrustIQ backend. |
   | `TRUSTIQ_API_KEY` | The API Key for authenticating requests to TrustIQ (defaults to `bob-trustiq-live-key-2026`). |
   | `GEMINI_API_KEY` | (Optional) Google Gemini API Key for identity check verifications. |
   | `GEMINI_MODEL` | (Optional) Gemini model to use (defaults to `gemini-2.0-flash`). |

4. **Deploy**: Click deploy! Vercel will host `index.html` as a static page on the edge and route API requests (e.g. `/api/*`) to the FastAPI serverless functions.

---

## Deployed Application

Once deployed on Vercel, the application is live at your custom Vercel domain, for example:
- **URL**: [https://bob-simulator.vercel.app/](https://bob-simulator.vercel.app/)

