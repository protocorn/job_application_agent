# Launchway - AI-Powered Job Application CLI

Automate your job search: tailor your resume with AI, find matching jobs, and apply autonomously.

---

## Installation

```bash
pip install launchway
```

On first launch, Launchway runs a short setup wizard where you can choose your AI provider
(or skip it entirely and decide later).  Browser binaries (Chromium) are downloaded automatically on first use.

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
| **Settings** | Manage your AI provider, email, and password |

---

## Requirements

- Python 3.11 or higher
- A [Launchway account](https://launchway.app) (free)
- No API key required to get started

---

## AI Provider

Launchway works **out of the box with no API key**.  On first launch you will be asked:

```
  1. Use Launchway AI  (recommended - no API key needed)
  2. Use my own Gemini API key
  3. Skip for now - decide later
```

**Option 1 (default):** Launchway's built-in AI handles everything. No key, no quota, no setup.

**Option 2:** Bring your own free [Google Gemini API key](https://aistudio.google.com).
Useful for power users who want full control over their own AI quota.

**Option 3:** Skip entirely and configure later from `Settings → AI Provider`.

You can switch between providers at any time from the **Settings** menu inside the CLI.

---

## How It Works

1. **Import your resume** - paste a Google Doc URL, upload a PDF/DOCX, or provide a LaTeX ZIP.
2. **Tailor** - Launchway rewrites your resume bullets to match a job description.
3. **Apply** - Launchway opens Chromium, navigates to the application form, and fills every field using your profile.

All your data (profile, applications) is stored securely in your Launchway account and synced across devices.

---

## Configuration

Settings are stored in `~/.launchway/.env`.

| Variable | Description | Default |
|---|---|---|
| `AI_PROVIDER` | `launchway` or `custom` | `launchway` |
| `GOOGLE_API_KEY` | Your Gemini key (only if `AI_PROVIDER=custom`) | - |
| `LAUNCHWAY_BACKEND_URL` | Override for self-hosted deployments | Production URL |

To override the backend URL for a self-hosted deployment:

```
LAUNCHWAY_BACKEND_URL=https://your-deployment.example.com
```

---

## Manual Browser Setup

If browser installation fails on first run:

```bash
python -m playwright install chromium
```

---

## License

MIT - see [LICENSE](LICENSE) for details.
