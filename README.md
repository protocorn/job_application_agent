# Launchway — AI-Powered Job Application CLI

Automate your job search: tailor your resume with AI, find matching jobs, and apply autonomously.

---

## Installation

```bash
pip install launchway
```

On first launch, Launchway will run a one-time setup wizard to save your Gemini API key.  
Browser binaries (Chromium) are downloaded automatically on first use.

---

## Quick Start

```bash
launchway
```

This opens the interactive menu where you can:

| Feature | Description |
|---|---|
| **Login / Sign Up** | Create or log into your Launchway account |
| **Profile** | Import your resume and set personal details |
| **Tailor Resume** | AI-tailor your resume for a specific job posting |
| **Job Search** | Find jobs matching your skills from multiple sources |
| **Auto Apply** | Autonomously fill and submit job applications |
| **Application History** | View all past applications |
| **Settings** | Manage API keys and preferences |

---

## Requirements

- Python 3.11 or higher
- A free [Google Gemini API key](https://aistudio.google.com) (for AI features)
- A [Launchway account](https://launchway.app) (free — for storing your profile and history)

---

## How It Works

1. **Import your resume** — paste a Google Doc URL, upload a PDF/DOCX, or provide a LaTeX ZIP.
2. **Tailor** — Launchway rewrites your resume bullets to match a job description using Gemini AI.
3. **Apply** — Launchway opens Chromium, navigates to the application form, and fills every field using your profile.

All your data (profile, applications, credits) is stored securely in your Launchway account and synced across devices.

---

## Configuration

Settings are stored in `~/.launchway/.env`.  
You can override the backend URL for self-hosted deployments:

```
LAUNCHWAY_BACKEND_URL=https://your-deployment.example.com
GOOGLE_API_KEY=AIzaSy...
```

---

## Manual Browser Setup

If browser installation fails, run:

```bash
python -m playwright install chromium
```

---

## License

MIT — see [LICENSE](LICENSE) for details.
