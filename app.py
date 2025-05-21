from flask import Flask, request, jsonify, render_template, send_from_directory, redirect
from edgar_pipeline import run_edgar_pipeline
import io
from contextlib import redirect_stdout
import os
import json
import traceback
from datetime import datetime, UTC, timedelta
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_limiter.errors import RateLimitExceeded
import threading
import string
import secrets
import zipfile
import tempfile
from functools import wraps
import redis


# === Load valid tickers ===
VALID_TICKERS = set()
with open("valid_tickers.csv", "r") as f:
    for line in f.readlines()[1:]:  # Skip header
        VALID_TICKERS.add(line.strip().upper())

# === Load valid keys ===
with open("valid_keys.json", "r") as f:
    key_map = json.load(f)

PUBLIC_KEY = key_map.get("public")
VALID_KEYS = set()
TIER_MAP = {}

for label, value in key_map.items():
    if isinstance(value, str):
        VALID_KEYS.add(value)
        TIER_MAP[value] = "public"
    elif isinstance(value, dict):
        # For structured entries like: "john": { "key": "...", "tier": "paid" }
        k = value.get("key")
        t = value.get("tier", "public") # fallback tier if missing
        if k:
            VALID_KEYS.add(k)
            TIER_MAP[k] = t

# === Define export folder ===
EXPORT_DIR = "exports"
os.makedirs(EXPORT_DIR, exist_ok=True)

app = Flask(__name__)
pipeline_lock = threading.Lock()

def get_user_key():
    return request.args.get("key", PUBLIC_KEY)

# === Rate limiting ===
def get_rate_limit_key():
    user_key = request.args.get("key", PUBLIC_KEY)
    user_tier = TIER_MAP.get(user_key, "public")
    
    # For public users, use IP address
    if user_tier == "public":
        return get_remote_address()
    # For registered/paid users, use their key
    return user_key

limiter = Limiter(
    key_func=get_rate_limit_key,
    app=app,
    default_limits=None,  # Remove default limit
    storage_uri="redis://localhost:6379/0",  # Use Redis for persistent storage
    default_limits_deduct_when=lambda response: response.status_code == 200,  # Only count successful requests
    storage_options={
        "socket_timeout": 5,
        "socket_connect_timeout": 5,
        "retry_on_timeout": True,
        "health_check_interval": 30
    }
)

# === Rate limit exceeded handler ===
@app.errorhandler(429)
def ratelimit_handler(e):
    # === API rate limit handler ===    
    if request.path.startswith("/run_pipeline"):
        user_key = request.args.get("key", "public")
        user_tier = TIER_MAP.get(user_key, "public")
        
        if user_tier == "public":
            message = "⚠️ Rate limit exceeded. Please wait or register for a free account to unlock more usage."
        elif user_tier == "registered":
            message = "⚠️ Rate limit exceeded. Please wait before trying again. Consider upgrading to unlock more usage."
        else:
            message = "⚠️ Rate limit exceeded. Please try again later."
            
        return jsonify({"status": "error", "message": message}), 429
    
    # === Trigger pipeline rate limit handler ===
    elif request.path.startswith("/trigger_pipeline"):
        user_key = request.args.get("key", PUBLIC_KEY)
        user_tier = TIER_MAP.get(user_key, "public")
        
        if user_tier == "public":
            message = "⚠️ You've reached the daily free limit. Please wait a bit, or register for a free API key with more access here: https://your.kartra.page/register."
        elif user_tier == "registered":
            message = "⚠️ You've reached your free daily limit. Please wait a bit before trying again. Consider upgrading to unlock more usage: https://your.kartra.page/register."
        else:
            message = "⚠️ Daily rate limit exceeded. Please try again later."
            
        return jsonify({"status": "error", "message": message}), 429
    
    # === Web UI rate limit handler ===
    user_key = request.args.get("key", PUBLIC_KEY)
    user_tier = TIER_MAP.get(user_key, "public")
    
    if user_tier == "public":
        output_text = (
            "⚠️ You've reached the daily free limit. "
            "Please wait a bit, or register for a free API key with more access here: https://your.kartra.page/register."
        )
    elif user_tier == "registered":
        output_text = (
            "⚠️ You've reached your free daily limit. "
            "Please wait a bit before trying again. Consider upgrading to unlock more usage: https://your.kartra.page/register."
        )
    else:
        output_text = "⚠️ Daily rate limit exceeded. Please try again later."
    
    log_request(None, None, None, user_key, "web", "rate_limited", user_tier)
    return render_template(
        "form.html",
        output_text=output_text,
        success_message="",
        excel_filename=None
    ), 429

# === Logging config ===
LOG_DIR = "error_logs"
os.makedirs(LOG_DIR, exist_ok=True)

# === Error logging ===
def log_error_json(source, context, exc, key=None, tier="public"):
    """Save error context and traceback to a JSON file."""
    error_info = {
        "timestamp": datetime.now(UTC).isoformat(),
        "source": source,
        "key": key,
        "tier": tier,
        **context,
        "error": str(exc),
        "traceback": traceback.format_exc()
    }
    # Use full_year_mode from context
    full_year_mode = context.get('full_year_mode', False)
    log_filename = f"{context.get('ticker', 'UNKNOWN')}_{context.get('quarter')}Q{context.get('year')}_{'FY' if full_year_mode else 'Q'}_error.json"
    log_path = os.path.join(LOG_DIR, log_filename)
    with open(log_path, "w") as f:
        json.dump(error_info, f, indent=2)
    return log_path

# === Usage logging ===
def log_usage(ticker, year, quarter, key, source, status="success", tier="public", full_year_mode=False):
    usage_record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "key": key,
        "ticker": ticker,
        "year": year,
        "quarter": quarter,
        "full_year_mode": full_year_mode,
        "source": source,
        "status": status, # "attempt", "denied", "rate_limited", "locked", etc.
        "tier": tier
    }

    os.makedirs("usage_logs", exist_ok=True)
    with open("usage_logs/usage_log.jsonl", "a") as f:
        f.write(json.dumps(usage_record) + "\n")

# === Request logging ===
def log_request(ticker, year, quarter, key, source, status="attempt", tier="public", full_year_mode=False):
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "key": key,
        "ticker": ticker,
        "year": year,
        "quarter": quarter,
        "full_year_mode": full_year_mode,
        "source": source,
        "status": status,  # "attempt", "denied", "rate_limited", "locked", etc.
        "tier": tier
    }

    os.makedirs("usage_logs", exist_ok=True)
    with open("usage_logs/request_log.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")

def create_zip_file(file_path):
    """Create a ZIP file containing the given file."""
    # Create a temporary file for the ZIP
    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    zip_filename = temp_zip.name
    temp_zip.close()
    
    # Create the ZIP file
    with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Add the file to the ZIP
        zipf.write(file_path, os.path.basename(file_path))
    
    return zip_filename

# === File download route ===
@app.route("/download/<filename>")
def download_file(filename):
    try:
        if not os.path.exists(EXPORT_DIR):
            print(f"DEBUG >> Export directory not found: {EXPORT_DIR}")
            return "Export directory not found", 404
            
        file_path = os.path.join(EXPORT_DIR, filename)
        if not os.path.exists(file_path):
            print(f"DEBUG >> File not found: {filename} in {EXPORT_DIR}")
            return "File not found", 404
            
        # If it's an XLSM file, serve it as a ZIP
        if filename.endswith('.xlsm'):
            zip_path = create_zip_file(file_path)
            try:
                return send_from_directory(
                    os.path.dirname(zip_path),
                    os.path.basename(zip_path),
                    as_attachment=True,
                    download_name=filename.replace('.xlsm', '.zip')
                )
            finally:
                # Clean up the temporary ZIP file
                os.unlink(zip_path)
        else:
            # For non-XLSM files, serve as is
            return send_from_directory(EXPORT_DIR, filename, as_attachment=True)
            
    except Exception as e:
        print(f"DEBUG >> Download error: {str(e)}")
        return str(e), 500

# === API endpoint ===
@app.route("/run_pipeline", methods=["POST"])
@limiter.limit(
    limit_value=lambda: {
        "public": "60 per day",     # 30 actions per day
        "registered": "120 per day",  # 60 actions per day
        "paid": "500 per day"      # 250 actions per day
    }[TIER_MAP.get(request.args.get("key", "public"), "public")],
    deduct_when=lambda response: response.status_code == 200  # Only count successful runs
)
def run_pipeline():
    user_key = request.args.get("key", PUBLIC_KEY)
    user_tier = TIER_MAP.get(user_key, "public")

    is_public = user_tier == "public"
    is_registered = user_tier == "registered"
    is_premium = user_tier == "paid"

    if user_key not in VALID_KEYS:
        log_request(None, None, None, user_key, "api", "denied", user_tier)
        return jsonify({"status": "error", "message": "❌ Invalid API key. Please check your link."})
    
    # 🔒 Try to acquire lock (non-blocking)
    if is_public:
        acquired = pipeline_lock.acquire(blocking=False)
    elif is_registered:
        acquired = pipeline_lock.acquire(timeout=20)
    elif is_premium:
        acquired = pipeline_lock.acquire(timeout=60)

    if not acquired:
        log_request(None, None, None, user_key, "api", "locked", user_tier)
        return jsonify({
            "status": "error",
            "message": "⚠️ Another request is currently processing. Please try again shortly."
        }), 429

    # === Process request === 
    try:
        data = request.json
        ticker = data['ticker'].strip().upper()
        if ticker not in VALID_TICKERS:
            log_request(ticker, None, None, user_key, "api", "denied", user_tier)
            return jsonify({"status": "error", "message": f"Sorry, '{ticker}' is not a supported stock. This updater only works for US-listed and US-based companies that file 10-K's and 10-Q's with the SEC."}), 400
        
        year = int(data['year'])
        quarter = int(data['quarter'])
        full_year_mode = data.get('full_year_mode', False)
        debug_mode = data.get('debug_mode', False)
        excel_file = data.get('excel_file', "Updater_EDGAR.xlsm")
        sheet_name = data.get('sheet_name', "Raw_data")

        # ✅ Build filename and check for existing output
        excel_filename = f"{ticker}_FY{str(year)[-2:]}_{excel_file}" if full_year_mode else f"{ticker}_{quarter}Q{str(year)[-2:]}_{excel_file}"
        updated_excel_file = os.path.join(EXPORT_DIR, excel_filename)

        # === Log directory and filename ===
        log_dir = "pipeline_logs"
        log_filename = f"{ticker}_FY{str(year)[-2:]}_{excel_file.replace('.xlsm', '')}_log.txt" if full_year_mode else f"{ticker}_{quarter}Q{str(year)[-2:]}_{excel_file.replace('.xlsm', '')}_log.txt"
        log_path = os.path.join(log_dir, log_filename)

        # === Check for cached output ===
        if os.path.exists(updated_excel_file) and os.path.exists(log_path):
            log_request(ticker, year, quarter, user_key, "api", "cache_hit", user_tier)
            return jsonify({
                "status": "success",
                "message": f"✅ File ready for {ticker} {quarter}Q{year}",
                "download_link": f"/download/{excel_filename}"
            }), 200
        
        # === Get output from pipeline ===
        output_buffer = io.StringIO()
        with redirect_stdout(output_buffer):
            # 🔧 Run the EDGAR pipeline
            run_edgar_pipeline(
                ticker,
                year,
                quarter,
                full_year_mode,
                debug_mode,
                excel_file,
                sheet_name
            )

        # === Log output ===
        log_text = output_buffer.getvalue()

        # Save log file
        log_dir = "pipeline_logs"
        os.makedirs(log_dir, exist_ok=True)

        log_filename = f"{ticker}_FY{str(year)[-2:]}_{excel_file.replace('.xlsm', '')}_log.txt" if full_year_mode else f"{ticker}_{quarter}Q{str(year)[-2:]}_{excel_file.replace('.xlsm', '')}_log.txt"
        log_path = os.path.join(log_dir, log_filename)

        with open(log_path, "w") as f:
            f.write(log_text)

        # === Log usage ===
        log_usage(
            ticker=ticker,
            year=year,
            quarter=quarter,
            key=user_key,
            tier=user_tier,
            source="api",
            status="success",
            full_year_mode=full_year_mode
        )

        # === Log request ===       
        log_request(ticker, year, quarter, user_key, "api", "success", user_tier)

        # ✅ Return JSON response with download link
        return jsonify({
            "status": "success",
            "message": f"Pipeline completed for {ticker} {quarter}Q{year}",
            "download_link": f"/download/{excel_filename}"
        }), 200

    # === Error handling ===
    except Exception as e:
        log_error_json("API", data, e, key=user_key, tier=user_tier)
        return jsonify({"status": "error", "message": str(e)}), 500

    # === Release lock ===
    finally:
        pipeline_lock.release()


# === Web UI form route ===
@app.route("/", methods=["GET", "POST"])
@limiter.limit(
    limit_value=lambda: {
        "public": "60 per day",     # 30 actions per day
        "registered": "120 per day",  # 60 actions per day
        "paid": "500 per day"      # 250 actions per day
    }[TIER_MAP.get(request.args.get("key", "public"), "public")],
    exempt_when=lambda: request.method == "GET",  # Only exempt GET requests
    deduct_when=lambda response: response.status_code == 200  # Only count successful runs
)
def web_ui():
    # === Get user key and tier ===
    user_key = request.args.get("key", PUBLIC_KEY)
    user_tier = TIER_MAP.get(user_key, "public")
    is_public = user_tier == "public"
    is_registered = user_tier == "registered"
    is_premium = user_tier == "paid"

    # === Check key validity ===
    if user_key not in VALID_KEYS:
        log_request(None, None, None, user_key, "web", "denied", user_tier)
        print("DEBUG >> ❌ Key not recognized!")
        return "❌ Access denied. Please use a valid link."

    # === Initialize variables ===
    output_text = ""
    success_message = ""
    excel_filename = None
    updated_excel_file = None

    # === Handle POST requests ===
    if request.method == "POST":
        # 🔒 Try to acquire lock first
        if user_tier == "public":
            acquired = pipeline_lock.acquire(blocking=False)
        elif user_tier == "registered":
            acquired = pipeline_lock.acquire(timeout=20)
        elif user_tier == "paid":
            acquired = pipeline_lock.acquire(timeout=60)
        # === Handle lock acquisition ===
        if not acquired:
            log_request(None, None, None, user_key, "web", "locked", user_tier)
            output_text = "⚠️ Another request is currently running. Please wait a moment and try again."
            return render_template("form.html", output_text=output_text, success_message="", excel_filename=None)

        # === Process request ===
        try:
            ticker = request.form.get("ticker", "").strip().upper()
            if ticker not in VALID_TICKERS:
                log_request(ticker, None, None, user_key, "web", "denied", user_tier)
                output_text = f"❌ Sorry, '{ticker}' is not a supported stock. This updater only works for US-listed and US-based companies that file 10-K's and 10-Q's with the SEC."
                return render_template("form.html", output_text=output_text, success_message="")
            
            year = int(request.form.get("year"))
            quarter = int(request.form.get("quarter"))
            full_year_mode = bool(request.form.get("full_year_mode"))
            debug_mode = bool(request.form.get("debug_mode"))
            excel_file = request.form.get("excel_file") or "Updater_EDGAR.xlsm"
            sheet_name = request.form.get("sheet_name") or "Raw_data"

            # ✅ Build full Excel file path
            excel_filename = f"{ticker}_FY{str(year)[-2:]}_{excel_file}" if full_year_mode else f"{ticker}_{quarter}Q{str(year)[-2:]}_{excel_file}"
            updated_excel_file = os.path.join(EXPORT_DIR, excel_filename)

            # === Log directory and filename ===
            log_dir = "pipeline_logs"
            log_filename = f"{ticker}_FY{str(year)[-2:]}_{excel_file.replace('.xlsm', '')}_log.txt" if full_year_mode else f"{ticker}_{quarter}Q{str(year)[-2:]}_{excel_file.replace('.xlsm', '')}_log.txt"
            log_path = os.path.join(log_dir, log_filename)

            # ✅ Check if file already exists & if so send to browser
            if os.path.exists(updated_excel_file) and os.path.exists(log_path):
                log_request(ticker, year, quarter, user_key, "web", "cache_hit", user_tier)
                success_message = f"✅ Updater file ready for {ticker} {quarter}Q{year} - unzip before opening! This was generated earlier — see prior output below."

                # Get the summary log
                with open(log_path, "r") as f:
                    log_text = f.read()
                    lines = log_text.splitlines()
                    
                    # Extract summary section
                    start_index = None
                    end_index = None
                    for i, line in enumerate(lines):
                        if "🔗 Target" in line and start_index is None:
                            start_index = i
                        if "⏱️ Total processing time" in line:
                            end_index = i + 1
                    
                    if start_index is not None and end_index is not None:
                        output_text = "\n".join(lines[start_index:end_index])
                    else:
                        output_text = log_text

                return render_template(
                    "form.html",
                    output_text=output_text,
                    success_message=success_message,
                    excel_filename=excel_filename
                )

            # ✅ Run pipeline and capture output
            output_buffer = io.StringIO()
            with redirect_stdout(output_buffer):
                run_edgar_pipeline(
                    ticker,
                    year,
                    quarter,
                    full_year_mode,
                    debug_mode,
                    excel_file,
                    sheet_name
                )

            # === Log usage ===
            log_usage(
                ticker=ticker,
                year=year,
                quarter=quarter,
                key=user_key,
                tier=user_tier,
                source="web",
                status="success",
                full_year_mode=full_year_mode
            )

            # === Get print outputs from the pipeline ===
            log_text = output_buffer.getvalue()
            lines = log_text.splitlines()

            # === Extract summary section ===
            start_index = None
            end_index = None

            for i, line in enumerate(lines):
                if "🔗 Target" in line and start_index is None:
                    start_index = i
                if "⏱️ Total processing time" in line:
                    end_index = i + 1

            if start_index is not None and end_index is not None:
                summary_text = "\n".join(lines[start_index:end_index])

                # === Show summary in the browser ===
                output_text = summary_text

                # === Save only the summary ===
                log_dir = "pipeline_logs"
                os.makedirs(log_dir, exist_ok=True)

                log_filename = f"{ticker}_FY{str(year)[-2:]}_{excel_file.replace('.xlsm', '')}_log.txt" if full_year_mode else f"{ticker}_{quarter}Q{str(year)[-2:]}_{excel_file.replace('.xlsm', '')}_log.txt"
                log_path = os.path.join(log_dir, log_filename)

                with open(log_path, "w") as f:
                    f.write(summary_text)
  
                success_message = f"✅ Excel updater file ready for {ticker} {quarter}Q{year} - unzip before opening!"
                log_request(ticker, year, quarter, user_key, "web", "success", user_tier)

                return render_template(
                    "form.html",
                    output_text=output_text,
                    success_message=success_message,
                    excel_filename=excel_filename
                )

            # === Handle error via failed summary ===
            else:
                summary_text = "⚠️ Processing did not complete. Kindly send an email to support@henrychien.com with the error message and we'll look into it."

                log_request(ticker, year, quarter, user_key, "web", "error", user_tier)

                return render_template(
                    "form.html",
                    output_text=summary_text,
                    success_message="❌ Sorry, this filing could not be completed successfully.",
                    excel_filename=None
                )

        # === Error handling ===
        except Exception as e:
            context = {
                "ticker": request.form.get("ticker"),
                "year": request.form.get("year"),
                "quarter": request.form.get("quarter"),
                "full_year_mode": request.form.get("full_year_mode"),
                "debug_mode": request.form.get("debug_mode"),
                "excel_file": request.form.get("excel_file"),
                "sheet_name": request.form.get("sheet_name"),
            }

            # === Log error & send message ===
            log_path = log_error_json("WebUI", context, e, key=user_key, tier=user_tier)
            output_text = (
                "This filing could not be processed.\n\n"
                f"Error details: {str(e)}\n\n"
                "If this is incorrect, please send an email to support@henrychien.com with the full message above.\n"
            )
            
            return render_template(
                "form.html",
                output_text=output_text,
                success_message="❌ Error occurred during processing",
                excel_filename=None
            )

        finally:
            # 🔓 Always release the lock after processing
            pipeline_lock.release()

    # === Render the form ===
    return render_template("form.html", output_text=output_text, success_message=success_message, excel_filename=excel_filename)

# === Generate key from Kartra ===
@app.route("/generate_key", methods=["POST"])
def generate_key_from_kartra():
    data = request.form or request.json
    user_email = data.get("email", "").strip().lower()
    tier = data.get("tier", "registered").strip().lower()  # now optional, defaults to "registered"

    if not user_email:
        return jsonify({"status": "error", "message": "Missing email"}), 400

    if tier not in ["registered", "paid"]:
        return jsonify({"status": "error", "message": "Invalid tier"}), 400

    # Load current keys
    with open("valid_keys.json", "r") as f:
        key_map = json.load(f)

    if user_email in key_map:
        return jsonify({
            "status": "error",
            "message": f"A key already exists for {user_email}"
        }), 409

    # Generate secure key
    alphabet = string.ascii_letters + string.digits
    new_key = ''.join(secrets.choice(alphabet) for _ in range(16))

    # Save to file
    key_map[user_email] = {
        "key": new_key,
        "tier": tier
    }

    # === Save to file ===
    with open("valid_keys.json", "w") as f:
        json.dump(key_map, f, indent=2)

    # === Return JSON response ===
    return jsonify({
        "status": "success",
        "email": user_email,
        "tier": tier,
        "api_key": new_key
    }), 200

@app.route("/trigger_pipeline", methods=["GET"])
@limiter.limit(
    limit_value=lambda: {
        "public": "60 per day",     # 30 actions per day
        "registered": "120 per day",  # 60 actions per day
        "paid": "500 per day"      # 250 actions per day
    }[TIER_MAP.get(request.args.get("key", "public"), "public")],
    deduct_when=lambda response: response.status_code == 200  # Only count successful runs
)
def trigger_pipeline():
    # === Get and validate parameters ===
    user_key = request.args.get("key", PUBLIC_KEY)
    user_tier = TIER_MAP.get(user_key, "public")
    
    # Validate key
    if user_key not in VALID_KEYS:
        log_request(None, None, None, user_key, "trigger", "denied", user_tier)
        return jsonify({"status": "error", "message": "❌ Invalid API key. Please check your link."}), 401
    
    # Get and validate ticker
    ticker = request.args.get("ticker", "").strip().upper()
    if not ticker or ticker not in VALID_TICKERS:
        log_request(ticker, None, None, user_key, "trigger", "denied", user_tier)
        return jsonify({"status": "error", "message": f"❌ Invalid or unsupported ticker: {ticker}"}), 400
    
    # Get and validate year/quarter
    try:
        year = int(request.args.get("year", ""))
        quarter = int(request.args.get("quarter", ""))
        if not (1 <= quarter <= 4):
            raise ValueError("Quarter must be between 1 and 4")
    except (ValueError, TypeError):
        log_request(ticker, None, None, user_key, "trigger", "denied", user_tier)
        return jsonify({"status": "error", "message": "❌ Invalid year or quarter format"}), 400
    
    # === Try to acquire lock ===
    is_public = user_tier == "public"
    is_registered = user_tier == "registered"
    is_premium = user_tier == "paid"
    
    if is_public:
        acquired = pipeline_lock.acquire(blocking=False)
    elif is_registered:
        acquired = pipeline_lock.acquire(timeout=20)
    elif is_premium:
        acquired = pipeline_lock.acquire(timeout=60)
    
    if not acquired:
        log_request(None, None, None, user_key, "trigger", "locked", user_tier)
        return jsonify({
            "status": "error",
            "message": "⚠️ Another request is currently processing. Please try again shortly."
        }), 429
    
    try:
        # === Set default parameters ===
        excel_file = "Updater_EDGAR.xlsm"
        sheet_name = "Raw_data"
        full_year_mode = request.args.get("full_year_mode", "").lower() == "true"
        debug_mode = False
        
        # === Build filenames ===
        base_filename = f"{ticker}_FY{str(year)[-2:]}" if full_year_mode else f"{ticker}_{quarter}Q{str(year)[-2:]}"
        xlsm_filename = f"{base_filename}_Updater_EDGAR.xlsm"
        xlsx_filename = f"{base_filename}_Updater_EDGAR.xlsx"
        updated_xlsm_file = os.path.join(EXPORT_DIR, xlsm_filename)
        updated_xlsx_file = os.path.join(EXPORT_DIR, xlsx_filename)
        
        # === Check for cached output ===
        if os.path.exists(updated_xlsx_file):
            log_request(ticker, year, quarter, user_key, "trigger", "cache_hit", user_tier)
            return redirect(f"/download/{xlsx_filename}")
        
        # === Run pipeline ===
        output_buffer = io.StringIO()
        with redirect_stdout(output_buffer):
            run_edgar_pipeline(
                ticker,
                year,
                quarter,
                full_year_mode,
                debug_mode,
                excel_file,
                sheet_name
            )
        
        # === Log usage ===
        log_usage(
            ticker=ticker,
            year=year,
            quarter=quarter,
            key=user_key,
            tier=user_tier,
            source="trigger",
            status="success",
            full_year_mode=full_year_mode
        )
        
        # === Log request ===
        log_request(ticker, year, quarter, user_key, "trigger", "success", user_tier)
        
        # === Redirect to download XLSX file ===
        return redirect(f"/download/{xlsx_filename}")
        
    except Exception as e:
        # === Log error ===
        context = {
            "ticker": ticker,
            "year": year,
            "quarter": quarter,
            "full_year_mode": full_year_mode,
            "debug_mode": debug_mode,
            "excel_file": excel_file,
            "sheet_name": sheet_name,
        }
        log_error_json("Trigger", context, e, key=user_key, tier=user_tier)
        return jsonify({"status": "error", "message": str(e)}), 500
        
    finally:
        # === Release lock ===
        pipeline_lock.release()

# === Admin authentication ===
def require_admin_token(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.args.get('token')
        if not token or token != os.getenv('ADMIN_TOKEN'):
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated_function

# === Admin routes ===
@app.route("/admin/usage_summary")
def usage_summary():
    # Get admin key from URL
    admin_key = request.args.get("key")
    
    # Check if it's a valid admin key
    if not admin_key or admin_key != os.getenv('ADMIN_KEY'):
        return jsonify({
            "status": "error",
            "message": "Unauthorized"
        }), 401
    
    try:
        # Load request logs
        requests = []
        with open("usage_logs/request_log.jsonl", "r") as f:
            for line in f:
                requests.append(json.loads(line.strip()))
        
        # Load usage logs
        usage = []
        with open("usage_logs/usage_log.jsonl", "r") as f:
            for line in f:
                usage.append(json.loads(line.strip()))
        
        # Load error logs
        error_logs = {}
        for filename in os.listdir("error_logs"):
            if filename.endswith("_error.json"):
                with open(os.path.join("error_logs", filename), "r") as f:
                    error_data = json.load(f)
                    # Use 4-digit year for consistency
                    year = error_data.get('year')
                    if isinstance(year, str) and len(year) == 2:
                        year = "20" + year
                    full_year = error_data.get('full_year_mode', False)
                    key = f"{error_data.get('ticker', 'UNKNOWN')}_{error_data.get('quarter')}Q{year}_{'FY' if full_year else 'Q'}"
                    error_logs[key] = error_data
        
        # Load summary metrics
        metrics_logs = {}
        metrics_dir = "metrics"
        for filename in os.listdir(metrics_dir):
            if filename.endswith("_summary_metrics.json"):
                try:
                    with open(os.path.join(metrics_dir, filename), "r") as f:
                        metrics_data = json.load(f)
                        # Extract ticker and quarter from filename (e.g., "AAPL_1Q24_summary_metrics.json")
                        parts = filename.split("_")
                        if len(parts) >= 3:
                            ticker = parts[0]
                            quarter = parts[1]
                            # Convert 2-digit year to 4-digit year
                            year = "20" + quarter[2:]  # Convert "Q24" to "2024"
                            # Check if it's a full year file
                            full_year = "FY" in filename
                            key = f"{ticker}_{quarter[0]}Q{year}_{'FY' if full_year else 'Q'}"
                            metrics_logs[key] = metrics_data
                except Exception as e:
                    print(f"Error loading metrics file {filename}: {str(e)}")
        
        # Combine the data using usage logs as primary index
        combined_data = []
        for usage_entry in usage:
            # Create base entry with usage data
            entry = {
                "usage": usage_entry,
                "error": None,
                "metrics": None,
                "related_requests": []  # Track all related requests
            }
            
            # Match error logs
            full_year = usage_entry.get('full_year_mode', False)
            error_key = f"{usage_entry['ticker']}_{usage_entry['quarter']}Q{usage_entry['year']}_{'FY' if full_year else 'Q'}"
            if error_key in error_logs:
                entry["error"] = error_logs[error_key]
            
            # Match metrics logs
            metrics_key = f"{usage_entry['ticker']}_{usage_entry['quarter']}Q{usage_entry['year']}_{'FY' if full_year else 'Q'}"
            if metrics_key in metrics_logs:
                entry["metrics"] = metrics_logs[metrics_key]
            
            # Find all related requests
            for req in requests:
                if (req.get("ticker") == usage_entry.get("ticker") and 
                    req.get("year") == usage_entry.get("year") and 
                    req.get("quarter") == usage_entry.get("quarter") and
                    req.get("full_year_mode", False) == usage_entry.get("full_year_mode", False)):
                    entry["related_requests"].append(req)
            
            combined_data.append(entry)
        
        # Sort by timestamp descending (most recent first)
        combined_data.sort(key=lambda x: x["usage"].get("timestamp", ""), reverse=True)
        
        return jsonify({
            "status": "success",
            "data": combined_data,
            "summary": {
                "total_usage_entries": len(combined_data),
                "total_requests": len(requests),
                "successful_requests": len([x for x in requests if x.get("status") == "success"]),
                "failed_requests": len([x for x in requests if x.get("status") == "error"]),
                "rate_limited_requests": len([x for x in requests if x.get("status") == "rate_limited"]),
                "cache_hits": len([x for x in requests if x.get("status") == "cache_hit"]),
                "by_tier": {
                    "public": len([x for x in requests if x.get("tier") == "public"]),
                    "registered": len([x for x in requests if x.get("tier") == "registered"]),
                    "paid": len([x for x in requests if x.get("tier") == "paid"])
                }
            }
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error aggregating logs: {str(e)}"
        }), 500

@app.route("/admin/check_key_usage")
def check_key_usage():
    # Get admin key from URL
    admin_key = request.args.get("key")
    
    # Check if it's a valid admin key
    if not admin_key or admin_key != os.getenv('ADMIN_KEY'):
        return jsonify({
            "status": "error",
            "message": "Unauthorized"
        }), 401
    
    # Get the key to check
    key_to_check = request.args.get("check_key")
    if not key_to_check:
        return jsonify({
            "status": "error",
            "message": "No key provided to check"
        }), 400
    
    try:
        # Get Redis connection
        redis_client = redis.Redis(host='localhost', port=6379, db=0)
        
        # Get the key's tier
        tier = TIER_MAP.get(key_to_check, "public")
        
        # Get the limit for this tier
        limit = {
            "public": "60 per day",
            "registered": "120 per day",
            "paid": "500 per day"
        }[tier]
        
        # Extract the number from the limit string (e.g., "60 per day" -> 60)
        limit_number = int(limit.split()[0])
        
        if tier == "public":
            # For public tier, get all keys (both IP-based and public key)
            patterns = [
                f"LIMITS:LIMITER/*/web_ui/{limit_number}/1/day",
                f"LIMITS:LIMITER/*/run_pipeline/{limit_number}/1/day",
                f"LIMITS:LIMITER/*/trigger_pipeline/{limit_number}/1/day"
            ]
            
            # Simple total usage counter and IP counts
            total_usage = 0
            ip_counts = {}
            
            for pattern in patterns:
                keys = redis_client.keys(pattern)
                for key in keys:
                    key_str = key.decode('utf-8')
                    parts = key_str.split('/')
                    if len(parts) >= 4:
                        identifier = parts[1]  # This is the IP or "public"
                        usage = int(redis_client.get(key) or 0)
                        if usage > 0:
                            total_usage += usage
                            if identifier not in ip_counts:
                                ip_counts[identifier] = 0
                            ip_counts[identifier] += usage
            
            return jsonify({
                "status": "success",
                "key": key_to_check,
                "tier": tier,
                "limit": limit,
                "current_usage": total_usage,
                "note": "Public tier is rate limited by IP address. This shows total usage across all IPs.",
                "ip_counts": ip_counts
            })
        else:
            # For registered/paid users, check their specific key
            web_ui_key = f"LIMITS:LIMITER/{key_to_check}/web_ui/{limit_number}/1/day"
            api_key = f"LIMITS:LIMITER/{key_to_check}/run_pipeline/{limit_number}/1/day"
            trigger_key = f"LIMITS:LIMITER/{key_to_check}/trigger_pipeline/{limit_number}/1/day"
            
            # Get usage from each route
            web_ui_usage = int(redis_client.get(web_ui_key) or 0)
            api_usage = int(redis_client.get(api_key) or 0)
            trigger_usage = int(redis_client.get(trigger_key) or 0)
            
            # Total usage is sum of all routes
            total_usage = web_ui_usage + api_usage + trigger_usage
            
            return jsonify({
                "status": "success",
                "key": key_to_check,
                "tier": tier,
                "limit": limit,
                "current_usage": total_usage,
                "breakdown": {
                    "web_ui": web_ui_usage,
                    "api": api_usage,
                    "trigger": trigger_usage
                }
            })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error checking key usage: {str(e)}"
        }), 500

if __name__ == "__main__": 
    app.run(port=5000)
