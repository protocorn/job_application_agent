"""
Bulk personalized email sender for Launchway templates.

Example (PowerShell):
python .\bulk_email_sender.py --emails "email1@gmail.com email2@gmail.com" --template 1
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Iterable

import requests
from dotenv import load_dotenv


TEMPLATE_1_SUBJECT = "You’ve completed step 1, here’s step 2 (Launchway)"
TEMPLATE_1_SETUP_LINK = "https://www.launchway.app/download"
TEMPLATE_2_SUBJECT = "Verify your email to continue (Launchway)"
TEMPLATE_2_VERIFY_LINK = "https://www.launchway.app/login"


def parse_email_list(raw_value: str) -> list[str]:
    """Parse space/comma/semicolon separated emails and dedupe while preserving order."""
    parts = re.split(r"[\s,;]+", (raw_value or "").strip())
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        candidate = part.strip()
        if not candidate:
            continue
        if candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
    return result


def name_from_email(email: str) -> str:
    """Infer a friendly first-name style label from email local-part."""
    local_part = email.split("@", 1)[0]
    cleaned = re.sub(r"[\d]+", " ", local_part)
    cleaned = re.sub(r"[._+\-]+", " ", cleaned).strip()
    if not cleaned:
        return "there"
    words = [w for w in cleaned.split() if w]
    if not words:
        return "there"
    return " ".join(word.capitalize() for word in words[:2])


def choose_template(template_arg: str | None) -> str:
    """Select the template to send."""
    if template_arg:
        normalized = str(template_arg).strip().lower()
        if normalized in {"1", "option1", "template1", "step2"}:
            return "template1"
        if normalized in {"2", "option2", "template2", "verify"}:
            return "template2"
        raise ValueError("Unsupported template. Use 1 or 2.")

    print("\nChoose email template:\n")
    print("1) Step 1 done. Here's step 2.")
    print("2) Signed up but not verified email reminder.")
    print("")

    while True:
        selected = input("Enter option number: ").strip().lower()
        if selected in {"1", "option1", "template1", "step2"}:
            return "template1"
        if selected in {"2", "option2", "template2", "verify"}:
            return "template2"
        print("Invalid option. Please type 1 or 2.")


def build_template_1_html(name: str, setup_link: str) -> str:
    return f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Launchway - Step 2 Setup</title>
    <style>
      body {{
        margin: 0;
        padding: 0;
        background: #ffffff;
        color: #1f2937;
        font-family: Inter, "Segoe UI", Arial, sans-serif;
      }}
      .container {{
        max-width: 600px;
        margin: 0 auto;
        padding: 40px 16px;
      }}
      .brand-row {{
        padding-bottom: 18px;
      }}
      .brand-logo {{
        width: 36px;
        height: 36px;
        border-radius: 50%;
        vertical-align: middle;
      }}
      .brand-text {{
        display: inline-block;
        margin-left: 10px;
        vertical-align: middle;
        color: #111827;
        font-size: 24px;
        font-weight: 700;
        letter-spacing: -0.02em;
        line-height: 1;
      }}
      .brand-way {{
        color: #06b6d4;
        font-weight: 400;
      }}
      .heading {{
        margin: 0 0 24px 0;
        color: #111827;
        font-size: 26px;
        line-height: 1.3;
        font-weight: 600;
      }}
      .copy {{
        padding: 0 0 16px 0;
        font-size: 16px;
        line-height: 1.7;
        color: #1f2937;
      }}
      .bullet-copy {{
        padding: 0 0 20px 22px;
        font-size: 16px;
        line-height: 1.8;
        color: #1f2937;
      }}
      .cta-link {{
        display: inline-block;
        background: #06b6d4;
        color: #ffffff;
        text-decoration: none;
        font-size: 15px;
        font-weight: 600;
        padding: 12px 20px;
        border-radius: 6px;
      }}
    </style>
  </head>
  <body>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#ffffff;">
      <tr>
        <td align="center">
          <table role="presentation" width="600" cellspacing="0" cellpadding="0" class="container" style="width:100%;">
            <tr class="brand-row">
              <td>
                <img
                  src="https://www.launchway.app/assets/launchwaylogo.png"
                  alt="Launchway Logo"
                  class="brand-logo"
                />
                <span class="brand-text">Launch<span class="brand-way">way</span></span>
              </td>
            </tr>
            <tr>
              <td>
                <h1 class="heading">
                  You've completed step 1, here's step 2
                </h1>
              </td>
            </tr>
            <tr>
              <td class="copy">
                Hey {name},
              </td>
            </tr>
            <tr>
              <td class="copy">
                Thanks again for signing up for Launchway beta.
              </td>
            </tr>
            <tr>
              <td class="copy">
                You've already completed <strong>step 1</strong>, signing up.
              </td>
            </tr>
            <tr>
              <td class="copy">
                Now comes <strong>step 2</strong>, setting up the CLI.
              </td>
            </tr>
            <tr>
              <td class="copy" style="padding-bottom:10px;">
                This one-time setup unlocks Launchway's main features:
              </td>
            </tr>
            <tr>
              <td class="bullet-copy">
                - resume tailoring<br />
                - relevant job search<br />
                - assisted job apply
              </td>
            </tr>
            <tr>
              <td class="copy" style="padding-bottom:20px;">
                I know CLI adds a bit of friction, but once it's set up, the goal is simple:
                <strong>save you time on repetitive job applications.</strong>
              </td>
            </tr>
            <tr>
              <td style="padding:0 0 10px 0; font-size:18px; line-height:1.5; color:#111827; font-weight:600;">
                Best way to try it
              </td>
            </tr>
            <tr>
              <td class="copy" style="padding-bottom:10px;">
                Use it on <strong>1 real job posting</strong>
              </td>
            </tr>
            <tr>
              <td class="bullet-copy">
                - upload/connect your resume<br />
                - paste a job description<br />
                - run resume tailoring
              </td>
            </tr>
            <tr>
              <td style="padding:6px 0 28px 0;">
                <a href="{setup_link}" class="cta-link" style="color:#ffffff !important; text-decoration:none !important;">
                  Set Up Launchway
                </a>
              </td>
            </tr>
            <tr>
              <td class="copy">
                If you get stuck anywhere, just reply with:
              </td>
            </tr>
            <tr>
              <td class="copy" style="padding-bottom:16px; color:#111827;">
                <strong>I got stuck at ___</strong>
              </td>
            </tr>
            <tr>
              <td class="copy" style="padding-bottom:22px;">
                Even a one-line reply helps a lot because I'm actively improving the beta based on where users struggle.
              </td>
            </tr>
            <tr>
              <td style="padding:0; font-size:16px; line-height:1.7; color:#1f2937;">
                Thanks again for being an early tester.<br /><br />
                Sahil, Founder, Launchway
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
    """.strip()


def build_template_1_text(name: str, setup_link: str) -> str:
    return f"""Subject: {TEMPLATE_1_SUBJECT}

Hey {name},

Thanks again for signing up for Launchway beta.

You've already completed step 1, signing up.
Now comes step 2, setting up the CLI.

This one-time setup unlocks Launchway's main features like:
- resume tailoring
- relevant job search
- assisted job apply

I know CLI adds a bit of friction, but once it's set up, the goal is simple:
save you time on repetitive job applications.

Best way to try it:
Use it on 1 real job posting

You can start by:
- uploading / connecting your resume
- pasting a job description
- running resume tailoring

Setup link:
{setup_link}

If you get stuck anywhere, just reply with:
I got stuck at ___

Even a one-line reply helps a lot because I'm actively improving the beta based on what users struggle with.

Thanks again for being an early tester.

Sahil, Founder, Launchway
"""


def build_template_2_html(name: str, verify_link: str) -> str:
    return f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Launchway - Verify Email</title>
    <style>
      body {{
        margin: 0;
        padding: 0;
        background: #ffffff;
        color: #1f2937;
        font-family: Inter, "Segoe UI", Arial, sans-serif;
      }}
      .container {{
        max-width: 600px;
        margin: 0 auto;
        padding: 40px 16px;
      }}
      .brand-row {{
        padding-bottom: 18px;
      }}
      .brand-logo {{
        width: 36px;
        height: 36px;
        border-radius: 50%;
        vertical-align: middle;
      }}
      .brand-text {{
        display: inline-block;
        margin-left: 10px;
        vertical-align: middle;
        color: #111827;
        font-size: 24px;
        font-weight: 700;
        letter-spacing: -0.02em;
        line-height: 1;
      }}
      .brand-way {{
        color: #06b6d4;
        font-weight: 400;
      }}
      .heading {{
        margin: 0 0 24px 0;
        color: #111827;
        font-size: 26px;
        line-height: 1.3;
        font-weight: 600;
      }}
      .copy {{
        padding: 0 0 16px 0;
        font-size: 16px;
        line-height: 1.7;
        color: #1f2937;
      }}
      .bullet-copy {{
        padding: 0 0 20px 22px;
        font-size: 16px;
        line-height: 1.8;
        color: #1f2937;
      }}
      .cta-link {{
        display: inline-block;
        background: #06b6d4;
        color: #ffffff;
        text-decoration: none;
        font-size: 15px;
        font-weight: 600;
        padding: 12px 20px;
        border-radius: 6px;
      }}
    </style>
  </head>
  <body>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#ffffff;">
      <tr>
        <td align="center">
          <table role="presentation" width="600" cellspacing="0" cellpadding="0" class="container" style="width:100%;">
            <tr class="brand-row">
              <td>
                <img
                  src="https://www.launchway.app/assets/launchwaylogo.png"
                  alt="Launchway Logo"
                  class="brand-logo"
                />
                <span class="brand-text">Launch<span class="brand-way">way</span></span>
              </td>
            </tr>
            <tr>
              <td>
                <h1 class="heading">
                  Quick reminder: please verify your email
                </h1>
              </td>
            </tr>
            <tr>
              <td class="copy">
                Hey {name},
              </td>
            </tr>
            <tr>
              <td class="copy">
                Thanks again for signing up for Launchway.
              </td>
            </tr>
            <tr>
              <td class="copy">
                You have been granted beta access.
                The only step left to get started is email verification.
              </td>
            </tr>
            <tr>
              <td class="copy" style="padding-bottom:10px;">
                Once verified, you can continue with:
              </td>
            </tr>
            <tr>
              <td class="bullet-copy">
                - resume tailoring for real job posts<br />
                - relevant job search and role discovery<br />
                - assisted job apply workflows<br />
                - dashboard, profile, and CLI setup
              </td>
            </tr>
            <tr>
              <td style="padding:6px 0 28px 0;">
                <a href="{verify_link}" class="cta-link" style="color:#ffffff !important; text-decoration:none !important;">
                  Verify Email
                </a>
              </td>
            </tr>
            <tr>
              <td class="copy">
                If your earlier verification link expired, sign in and request a fresh verification email.
              </td>
            </tr>
            <tr>
              <td class="copy" style="padding-bottom:22px;">
                You will receive the next email soon with step-by-step setup guidance.
              </td>
            </tr>
            <tr>
              <td class="copy" style="padding-bottom:22px;">
                If you get stuck, reply with: <strong>I got stuck at ___</strong>
              </td>
            </tr>
            <tr>
              <td style="padding:0; font-size:16px; line-height:1.7; color:#1f2937;">
                Sahil, Founder, Launchway
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
    """.strip()


def build_template_2_text(name: str, verify_link: str) -> str:
    return f"""Subject: {TEMPLATE_2_SUBJECT}

Hey {name},

Thanks again for signing up for Launchway.

You have been granted beta access.
The only step left to get started is email verification.

Once verified, you can continue with:
- resume tailoring for real job posts
- relevant job search and role discovery
- assisted job apply workflows
- dashboard, profile, and CLI setup

Verify here:
{verify_link}

If your earlier verification link expired, sign in and request a fresh verification email.

You will receive the next email soon with step-by-step setup guidance.

If you get stuck, reply with:
I got stuck at ___

Sahil, Founder, Launchway
"""


def send_resend_email(
    *,
    resend_api_key: str,
    from_email: str,
    to_email: str,
    subject: str,
    html: str,
    text: str,
) -> tuple[bool, str]:
    response = requests.post(
        "https://api.resend.com/emails",
        json={
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "html": html,
            "text": text,
        },
        headers={
            "Authorization": f"Bearer {resend_api_key}",
            "Content-Type": "application/json",
        },
        timeout=20,
    )
    if response.status_code == 200:
        return True, "sent"
    return False, f"{response.status_code}: {response.text}"


def iter_payloads(template_key: str, emails: Iterable[str]):
    for email in emails:
        recipient_name = name_from_email(email)
        if template_key == "template1":
            yield {
                "to_email": email,
                "subject": TEMPLATE_1_SUBJECT,
                "html": build_template_1_html(recipient_name, TEMPLATE_1_SETUP_LINK),
                "text": build_template_1_text(recipient_name, TEMPLATE_1_SETUP_LINK),
            }
        if template_key == "template2":
            yield {
                "to_email": email,
                "subject": TEMPLATE_2_SUBJECT,
                "html": build_template_2_html(recipient_name, TEMPLATE_2_VERIFY_LINK),
                "text": build_template_2_text(recipient_name, TEMPLATE_2_VERIFY_LINK),
            }


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Send personalized Launchway campaign emails."
    )
    parser.add_argument(
        "--emails",
        required=True,
        help='Space/comma/semicolon separated emails. Example: --emails "a@x.com b@y.com"',
    )
    parser.add_argument(
        "--template",
        default=None,
        help="Template id. Leave empty to choose interactively. (Supported: 1, 2)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print recipients and subject without sending.",
    )
    args = parser.parse_args()

    parsed_emails = parse_email_list(args.emails)
    if not parsed_emails:
        print("No valid emails were provided.")
        return 1

    template_key = choose_template(args.template)
    resend_api_key = os.getenv("RESEND_API_KEY", "").strip()
    from_email = os.getenv("FROM_EMAIL", "onboarding@resend.dev").strip()

    if not args.dry_run and not resend_api_key:
        print("RESEND_API_KEY is missing in your environment/.env.")
        return 1

    print(f"\nTemplate: {template_key}")
    print(f"Total recipients: {len(parsed_emails)}")
    print("Recipients:")
    for recipient in parsed_emails:
        print(f"- {recipient}")

    if not args.dry_run:
        confirm = input("\nSend now? (y/N): ").strip().lower()
        if confirm not in {"y", "yes"}:
            print("Cancelled.")
            return 0

    success_count = 0
    failure_count = 0
    for payload in iter_payloads(template_key, parsed_emails):
        if args.dry_run:
            print(f"[DRY-RUN] {payload['to_email']} -> {payload['subject']}")
            success_count += 1
            continue
        ok, message = send_resend_email(
            resend_api_key=resend_api_key,
            from_email=from_email,
            to_email=payload["to_email"],
            subject=payload["subject"],
            html=payload["html"],
            text=payload["text"],
        )
        if ok:
            print(f"[SENT] {payload['to_email']}")
            success_count += 1
        else:
            print(f"[FAILED] {payload['to_email']} -> {message}")
            failure_count += 1

    print("\nDone.")
    print(f"Success: {success_count}")
    print(f"Failed:  {failure_count}")
    return 0 if failure_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
