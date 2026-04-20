import hashlib
import json
import logging
import os

from flask import Blueprint, jsonify, request


def create_public_blueprint() -> Blueprint:
    """Create public/root routes."""
    public_bp = Blueprint("public", __name__)

    allowed_reactions = {
        "🔥": "I need this right now",
        "👀": "I'm keeping an eye on it",
        "🤔": "I still have questions",
        "😬": "Not for me",
    }

    def _get_ip_hash() -> str:
        ip = (
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.headers.get("X-Real-IP", "")
            or request.remote_addr
            or "unknown"
        )
        return hashlib.sha256(ip.encode()).hexdigest()

    @public_bp.route("/pick-resume", methods=["GET"])
    def pick_resume_page():
        jwt_token = request.args.get("token", "")
        backend_url = request.host_url.rstrip("/")
        forwarded_proto = (
            request.headers.get("X-Forwarded-Proto") or ""
        ).split(",")[0].strip().lower()
        if forwarded_proto == "https" and backend_url.startswith("http://"):
            backend_url = "https://" + backend_url[len("http://") :]
        if backend_url.startswith("http://") and "railway.app" in backend_url:
            backend_url = "https://" + backend_url[len("http://") :]

        picker_api_key = os.getenv("GOOGLE_PICKER_API_KEY", "")
        google_client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        google_picker_app_id = (
            os.getenv("GOOGLE_PICKER_APP_ID", "").strip()
            or os.getenv("GOOGLE_CLOUD_PROJECT_NUMBER", "").strip()
            or os.getenv("GOOGLE_PROJECT_NUMBER", "").strip()
        )

        page_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Select Resume — Launchway</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #0f1117;
      color: #e2e8f0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .card {{
      background: #1a1d2e;
      border: 1px solid #2d3148;
      border-radius: 16px;
      padding: 48px 40px;
      max-width: 480px;
      width: 90%;
      text-align: center;
    }}
    .logo {{ font-size: 28px; font-weight: 700; color: #6c63ff; margin-bottom: 8px; }}
    h1 {{ font-size: 20px; font-weight: 600; margin-bottom: 8px; }}
    p {{ color: #94a3b8; font-size: 14px; line-height: 1.6; margin-bottom: 24px; }}
    button {{
      background: #6c63ff;
      color: #fff;
      border: none;
      border-radius: 8px;
      padding: 12px 28px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s;
      width: 100%;
    }}
    button:hover {{ background: #574fd6; }}
    button:disabled {{ background: #3d3d5c; cursor: not-allowed; }}
    .status {{
      margin-top: 20px;
      padding: 12px;
      border-radius: 8px;
      font-size: 14px;
      display: none;
    }}
    .status.success {{ background: #0d2e1a; color: #4ade80; border: 1px solid #166534; display: block; }}
    .status.error   {{ background: #2e0d0d; color: #f87171; border: 1px solid #991b1b; display: block; }}
    .status.info    {{ background: #0d1a2e; color: #60a5fa; border: 1px solid #1e40af; display: block; }}
    .filename {{ font-weight: 600; color: #a78bfa; word-break: break-all; }}
    .return-hint {{ margin-top: 16px; font-size: 13px; color: #64748b; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">Launchway</div>
    <h1>Select Your Resume</h1>
    <p>Click the button below to pick your Google Doc resume from Drive. Once selected, Launchway will process it automatically.</p>
    <button id="pick-btn">
      Open Google Drive Picker
    </button>
    <div id="status" class="status"></div>
    <p id="return-hint" class="return-hint"></p>
  </div>

  <script src="https://apis.google.com/js/api.js"></script>
  <script>
    const JWT_TOKEN   = {json.dumps(jwt_token)};
    const BACKEND_URL = {json.dumps(backend_url)};
    const API_KEY     = {json.dumps(picker_api_key)};
    const CLIENT_ID   = {json.dumps(google_client_id)};
    const APP_ID      = {json.dumps(google_picker_app_id)};

    let pickerApiLoaded = false;
    let accessToken     = null;

    document.addEventListener('DOMContentLoaded', function() {{
      document.getElementById('return-hint').style.display = 'none';
      document.getElementById('pick-btn').addEventListener('click', openPicker);
    }});

    function setStatus(msg, type) {{
      const el = document.getElementById('status');
      el.textContent = msg;
      el.className = 'status ' + (type || '');
      if (!type) el.style.display = 'none';
    }}

    async function fetchAccessToken() {{
      const resp = await fetch(BACKEND_URL + '/api/oauth/access-token', {{
        headers: {{ Authorization: 'Bearer ' + JWT_TOKEN }}
      }});
      if (!resp.ok) throw new Error('Google account not connected. Please connect it in the Launchway app first.');
      const data = await resp.json();
      return data.access_token;
    }}

    async function processResumeFile(fileId, fileName) {{
      const resumeUrl = 'https://docs.google.com/document/d/' + fileId + '/edit';
      setStatus('Processing "' + fileName + '"… this may take up to 30 seconds.', 'info');
      const resp = await fetch(BACKEND_URL + '/api/process-resume', {{
        method: 'POST',
        headers: {{
          'Content-Type': 'application/json',
          Authorization: 'Bearer ' + JWT_TOKEN
        }},
        body: JSON.stringify({{ resume_url: resumeUrl }})
      }});
      const data = await resp.json();
      if (!resp.ok || !data.success) throw new Error(data.error || 'Processing failed');
      return fileName;
    }}

    function pickerCallback(data) {{
      if (data[google.picker.Response.ACTION] === google.picker.Action.PICKED) {{
        const doc  = data[google.picker.Response.DOCUMENTS][0];
        const id   = doc[google.picker.Document.ID];
        const name = doc[google.picker.Document.NAME];

        document.getElementById('pick-btn').disabled = true;
        processResumeFile(id, name)
          .then(function(name) {{
            setStatus('✓ Resume "' + name + '" selected and processed successfully!', 'success');
            const hint = document.getElementById('return-hint');
            hint.innerHTML = '✓ Done! Switch back to your terminal and press <strong>Enter</strong> to continue.';
            hint.style.display = 'block';
            document.getElementById('pick-btn').style.display = 'none';
          }})
          .catch(function(err) {{
            setStatus('Error: ' + err.message, 'error');
            document.getElementById('pick-btn').disabled = false;
          }});
      }}
    }}

    function createPicker() {{
      const view = new google.picker.DocsView(google.picker.ViewId.DOCS)
        .setMimeTypes('application/vnd.google-apps.document')
        .setMode(google.picker.DocsViewMode.LIST);

      let builder = new google.picker.PickerBuilder()
        .addView(view)
        .setOrigin(window.location.protocol + '//' + window.location.host)
        .setOAuthToken(accessToken)
        .setDeveloperKey(API_KEY)
        .setCallback(pickerCallback)
        .setTitle('Select your resume Google Doc');
      if (APP_ID) {{
        builder = builder.setAppId(APP_ID);
      }}
      const picker = builder.build();
      picker.setVisible(true);
    }}

    async function openPicker() {{
      try {{
        document.getElementById('pick-btn').disabled = true;
        setStatus('Connecting to Google…', 'info');

        if (!accessToken) {{
          accessToken = await fetchAccessToken();
        }}

        if (!pickerApiLoaded) {{
          await new Promise(function(resolve, reject) {{
            gapi.load('picker', {{ callback: resolve, onerror: reject }});
          }});
          pickerApiLoaded = true;
        }}

        setStatus('', '');
        document.getElementById('pick-btn').disabled = false;
        createPicker();
      }} catch (err) {{
        setStatus('Error: ' + err.message, 'error');
        document.getElementById('pick-btn').disabled = false;
      }}
    }}
  </script>
</body>
</html>"""
        return page_html, 200, {"Content-Type": "text/html; charset=utf-8"}

    @public_bp.route("/", methods=["GET"])
    def root():
        return jsonify(
            {"status": "ok", "service": "Job Application Agent API", "version": "1.0.0"}
        )

    @public_bp.route("/api/page-reactions", methods=["GET", "OPTIONS"])
    def get_page_reactions():
        try:
            from database_config import PageReaction, PageVisit, SessionLocal

            db = SessionLocal()
            try:
                total_reactions = db.query(PageReaction).count()
                reactions = (
                    db.query(PageReaction).order_by(PageReaction.created_at.desc()).limit(50).all()
                )
                visitor_count = db.query(PageVisit).count()
                return (
                    jsonify(
                        {
                            "reactions": [
                                {"emoji": r.emoji, "label": r.label, "id": r.id} for r in reactions
                            ],
                            "total_reactions": total_reactions,
                            "visitor_count": visitor_count,
                        }
                    ),
                    200,
                )
            finally:
                db.close()
        except Exception as exc:
            logging.error(f"Error fetching page reactions: {exc}")
            return jsonify({"error": "Failed to fetch reactions"}), 500

    @public_bp.route("/api/page-reactions", methods=["POST"])
    def post_page_reaction():
        try:
            data = request.get_json(silent=True) or {}
            emoji = data.get("emoji", "").strip()
            label = allowed_reactions.get(emoji)

            if not label:
                return jsonify({"error": "Invalid reaction"}), 400

            ip_hash = _get_ip_hash()
            is_dev = os.getenv("FLASK_ENV", "production") == "development"

            from database_config import PageReaction, SessionLocal

            db = SessionLocal()
            try:
                existing = db.query(PageReaction).filter_by(ip_hash=ip_hash).first()
                if existing and not is_dev:
                    return (
                        jsonify(
                            {
                                "error": "already_reacted",
                                "reaction": {"emoji": existing.emoji, "label": existing.label},
                            }
                        ),
                        409,
                    )
                if existing and is_dev:
                    db.delete(existing)
                    db.flush()

                reaction = PageReaction(ip_hash=ip_hash, emoji=emoji, label=label)
                db.add(reaction)
                db.commit()
                reaction_number = db.query(PageReaction).count()
                return (
                    jsonify(
                        {
                            "success": True,
                            "reaction": {"emoji": emoji, "label": label},
                            "reaction_number": reaction_number,
                        }
                    ),
                    201,
                )
            finally:
                db.close()
        except Exception as exc:
            logging.error(f"Error saving page reaction: {exc}")
            return jsonify({"error": "Failed to save reaction"}), 500

    @public_bp.route("/api/page-reactions/mine", methods=["DELETE"])
    def delete_my_reaction():
        try:
            ip_hash = _get_ip_hash()
            from database_config import PageReaction, SessionLocal

            db = SessionLocal()
            try:
                deleted = db.query(PageReaction).filter_by(ip_hash=ip_hash).delete()
                db.commit()
                return jsonify({"success": True, "deleted": deleted}), 200
            finally:
                db.close()
        except Exception as exc:
            logging.error(f"Error deleting reaction: {exc}")
            return jsonify({"error": "Failed to delete reaction"}), 500

    @public_bp.route("/api/page-visits", methods=["POST"])
    def record_page_visit():
        try:
            ip_hash = _get_ip_hash()
            from database_config import PageVisit, SessionLocal

            db = SessionLocal()
            try:
                existing = db.query(PageVisit).filter_by(ip_hash=ip_hash).first()
                if not existing:
                    db.add(PageVisit(ip_hash=ip_hash))
                    db.commit()
                count = db.query(PageVisit).count()
                return jsonify({"visitor_count": count}), 200
            finally:
                db.close()
        except Exception as exc:
            logging.error(f"Error recording page visit: {exc}")
            return jsonify({"error": "Failed to record visit"}), 500

    return public_bp

