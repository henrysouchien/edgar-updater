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
limiter = Limiter(
    key_func=get_user_key,
    app=app,
    default_limits=["60 per day"],  # Match public tier limit
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
    
    # === Web UI rate limit handler ===
    user_key = request.args.get("key", PUBLIC_KEY)
    user_tier = TIER_MAP.get(user_key, "public")
    
    if user_tier == "public":
        output_text = (
            "⚠️ You've reached the daily free limit. "
            "Please wait a bit, or register for more free access here: https://your.kartra.page/register."
        )
    elif user_tier == "registered":
        output_text = (
            "⚠️ You've reached your dailylimit. "
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

# Debug function to print limiter state
def debug_limiter_state(user_key, user_tier):
    print(f"DEBUG >> Rate Limit State:")
    print(f"DEBUG >> - User Key: {user_key}")
    print(f"DEBUG >> - User Tier: {user_tier}")

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
    log_filename = f"{context.get('ticker', 'UNKNOWN')}_{context.get('quarter')}Q{context.get('year')}_error.json"
    log_path = os.path.join(LOG_DIR, log_filename)
    with open(log_path, "w") as f:
        json.dump(error_info, f, indent=2)
    return log_path

# === Usage logging ===
def log_usage(ticker, year, quarter, key, source, status="success", tier="public"):
    usage_record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "key": key,
        "ticker": ticker,
        "year": year,
        "quarter": quarter,
        "source": source,
        "status": status, # "attempt", "denied", "rate_limited", "locked", etc.
        "tier": tier
    }

    os.makedirs("usage_logs", exist_ok=True)
    with open("usage_logs/usage_log.jsonl", "a") as f:
        f.write(json.dumps(usage_record) + "\n")

# === Request logging ===
def log_request(ticker, year, quarter, key, source, status="attempt", tier="public"):
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "key": key,
        "ticker": ticker,
        "year": year,
        "quarter": quarter,
        "source": source,
        "status": status,  # "attempt", "denied", "rate_limited", "locked", etc.
        "tier": tier
    }

    os.makedirs("usage_logs", exist_ok=True)
    with open("usage_logs/request_log.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")

# === File download route ===
@app.route("/download/<filename>")
def download_file(filename):
    try:
        if not os.path.exists(EXPORT_DIR):
            print(f"DEBUG >> Export directory not found: {EXPORT_DIR}")
            return "Export directory not found", 404
        if not os.path.exists(os.path.join(EXPORT_DIR, filename)):
            print(f"DEBUG >> File not found: {filename} in {EXPORT_DIR}")
            return "File not found", 404
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
    
    # Debug logging for rate limiting
    print(f"\nDEBUG >> API Rate Limit Check:")
    print(f"DEBUG >> - User Key: {user_key}")
    print(f"DEBUG >> - User Tier: {user_tier}")
    print(f"DEBUG >> - Is Exempt: {TIER_MAP.get(user_key, 'public') == 'paid'}\n")

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
        ticker = data['ticker'].upper()
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
                "message": f"✅ Updater file ready for {ticker} {quarter}Q{year}",
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
            status="success"
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
        # Print debug info
        print(f"DEBUG >> User key: {user_key}")
        print(f"DEBUG >> User tier: {user_tier}")
        debug_limiter_state(user_key, user_tier)

        # 🔒 Try to acquire lock first
        if is_public:
            acquired = pipeline_lock.acquire(blocking=False)
        elif is_registered:
            acquired = pipeline_lock.acquire(timeout=20)
        elif is_premium:
            acquired = pipeline_lock.acquire(timeout=60)
        # === Handle lock acquisition ===
        if not acquired:
            log_request(None, None, None, user_key, "web", "locked", user_tier)
            output_text = "⚠️ Another request is currently running. Please wait a moment and try again."
            return render_template("form.html", output_text=output_text, success_message="", excel_filename=None)

        # === Process request ===
        try:
            ticker = request.form.get("ticker").upper()
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
                success_message = f"✅ Updater macro file ready with data for {ticker} {quarter}Q{year}! It was generated earlier and matched your request — see prior output below."

                # Get the summary log
                with open(log_path, "r") as f:
                    log_text = f.read()
                    lines = log_text.splitlines()
                    
                    # Extract summary section
                    start_index = None
                    end_index = None
                    for i, line in enumerate(lines):
                        if "📄 Export summary:" in line and start_index is None:
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
                status="success"
            )

            # === Get print outputs from the pipeline ===
            log_text = output_buffer.getvalue()
            lines = log_text.splitlines()

            # === Extract summary section ===
            start_index = None
            end_index = None

            for i, line in enumerate(lines):
                if "📄 Export summary:" in line and start_index is None:
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
  
                success_message = f"✅ Updater macro file completed with data for {ticker} {quarter}Q{year}!"
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
                "❌ This filing could not be processed.\n\n"
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
    ticker = request.args.get("ticker", "").upper()
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
            status="success"
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

if __name__ == "__main__":
    app.run(port=5000)
