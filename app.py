from flask import Flask, request, jsonify, render_template, send_from_directory
from edgar_pipeline import run_edgar_pipeline
import io
from contextlib import redirect_stdout
import os
import json
import traceback
from datetime import datetime, UTC
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_limiter.errors import RateLimitExceeded
import threading


# === Load valid tickers ===
VALID_TICKERS = set()
with open("valid_tickers.csv", "r") as f:
    for line in f.readlines()[1:]:  # Skip header
        VALID_TICKERS.add(line.strip().upper())

# === Load valid keys ===
with open("valid_keys.json", "r") as f:
    key_map = json.load(f)

VALID_KEYS = set(key_map.values())
PUBLIC_KEY = key_map.get("public")

# === Define export folder ===
EXPORT_DIR = "exports"
os.makedirs(EXPORT_DIR, exist_ok=True)

app = Flask(__name__)

pipeline_lock = threading.Lock()

def get_user_key():
    return request.args.get("key", PUBLIC_KEY)

limiter = Limiter(
    key_func=get_user_key,
    app=app,
    default_limits=[]  # We'll define per-route
)

web_ui_limit = limiter.shared_limit(
    limit_value=lambda: "5 per hour" if get_user_key() == PUBLIC_KEY else "100 per hour",
    scope="web_ui",
    per_method=["POST"],
    methods=["POST"]
)

# === Logging config ===
LOG_DIR = "error_logs"
os.makedirs(LOG_DIR, exist_ok=True)

def log_error_json(source, context, exc):
    """Save error context and traceback to a JSON file."""
    error_info = {
        "timestamp": datetime.now(UTC).isoformat(),
        "source": source,
        **context,
        "error": str(exc),
        "traceback": traceback.format_exc()
    }
    log_filename = f"{context.get('ticker', 'UNKNOWN')}_{context.get('quarter')}Q{context.get('year')}_error.json"
    log_path = os.path.join(LOG_DIR, log_filename)
    with open(log_path, "w") as f:
        json.dump(error_info, f, indent=2)
    return log_path

def log_usage(ticker, year, quarter, key, source, status="success"):
    usage_record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "key": key,
        "ticker": ticker,
        "year": year,
        "quarter": quarter,
        "source": source,
        "status": status # "attempt", "denied", "rate_limited", "locked", etc.
    }

    os.makedirs("usage_logs", exist_ok=True)
    with open("usage_logs/usage_log.jsonl", "a") as f:
        f.write(json.dumps(usage_record) + "\n")

def log_request(ticker, year, quarter, key, source, status="attempt"):
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "key": key,
        "ticker": ticker,
        "year": year,
        "quarter": quarter,
        "source": source,
        "status": status  # "attempt", "denied", "rate_limited", "locked", etc.
    }

    os.makedirs("usage_logs", exist_ok=True)
    with open("usage_logs/request_log.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")

# === File download route ===
@app.route("/download/<filename>")
def download_file(filename):
    return send_from_directory(EXPORT_DIR, filename, as_attachment=True)

# === API endpoint ===
@app.route("/run_pipeline", methods=["POST"])
@limiter.limit("3 per hour")
def run_pipeline():
    user_key = request.args.get("key", PUBLIC_KEY)
    if user_key not in VALID_KEYS:
        log_request(None, None, None, user_key, "api", "denied")
        return jsonify({"status": "error", "message": "❌ Invalid API key. Please check your link."})
    
    is_public = user_key == PUBLIC_KEY

    # 🔒 Try to acquire lock (non-blocking)
    if not pipeline_lock.acquire(blocking=False):
        log_request(None, None, None, user_key, "api", "locked")
        return jsonify({
            "status": "error",
            "message": "⚠️ Another request is currently processing. Please try again shortly."
        }), 429

    try:
        data = request.json
        ticker = data['ticker'].upper()
        if ticker not in VALID_TICKERS:
            log_request(ticker, None, None, user_key, "api", "denied")
            return jsonify({"status": "error", "message": f"Sorry, '{ticker}' is not a supported stock. This updater only works for US-listed and US-based companies that file 10-K's and 10-Q's with the SEC."}), 400
        
        year = int(data['year'])
        quarter = int(data['quarter'])
        full_year_mode = data.get('full_year_mode', False)
        debug_mode = data.get('debug_mode', False)
        excel_file = data.get('excel_file', "Updater_EDGAR.xlsm")
        sheet_name = data.get('sheet_name', "Raw_data")

        # ✅ Build filename and check for existing output
        excel_filename = f"{ticker}_{quarter}Q{str(year)[-2:]}_{excel_file}"
        updated_excel_file = os.path.join(EXPORT_DIR, excel_filename)

        log_dir = "pipeline_logs"
        log_filename = f"{ticker}_{quarter}Q{str(year)[-2:]}_{excel_file.replace('.xlsm', '')}_log.txt"
        log_path = os.path.join(log_dir, log_filename)

        if os.path.exists(updated_excel_file) and os.path.exists(log_path):
            log_request(ticker, year, quarter, user_key, "api", "cache_hit")
            return jsonify({
                "status": "success",
                "message": f"✅ Updater file ready for {ticker} {quarter}Q{year}",
                "download_link": f"/download/{excel_filename}"
            }), 200
        
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

        log_text = output_buffer.getvalue()

        # Save log file
        log_dir = "pipeline_logs"
        os.makedirs(log_dir, exist_ok=True)

        log_filename = f"{ticker}_{quarter}Q{str(year)[-2:]}_{excel_file.replace('.xlsm', '')}_log.txt"
        log_path = os.path.join(log_dir, log_filename)

        with open(log_path, "w") as f:
            f.write(log_text)

        log_usage(
            ticker=ticker,
            year=year,
            quarter=quarter,
            key=user_key,
            source="api",
            status="success"
        )

        log_request(ticker, year, quarter, user_key, "api", "success")

        # ✅ Return JSON response with download link
        return jsonify({
            "status": "success",
            "message": f"Pipeline completed for {ticker} {quarter}Q{year}",
            "download_link": f"/download/{excel_filename}"
        }), 200

    except Exception as e:
        log_error_json("API", data, e)
        return jsonify({"status": "error", "message": str(e)}), 500

    finally:
        pipeline_lock.release()

# === Web UI form route ===
@app.route("/", methods=["GET", "POST"])
def web_ui():
    user_key = request.args.get("key", PUBLIC_KEY)
    print("DEBUG >> user_key =", repr(user_key))
    print("DEBUG >> VALID_KEYS =", VALID_KEYS)
    if user_key not in VALID_KEYS:
        log_request(None, None, None, user_key, "web", "denied")
        print("DEBUG >> ❌ Key not recognized!")
        return "❌ Access denied. Please use a valid link."
    
    is_public = user_key == PUBLIC_KEY
    
    output_text = ""
    success_message = ""
    excel_filename = None
    updated_excel_file = None

    if request.method == "POST":
        try:
            limiter.check()  # ✅ Manually enforce rate limit for this request
        except RateLimitExceeded:
            log_request(None, None, None, user_key, "web", "rate_limited")
            output_text = "⚠️ Rate limit exceeded. Please wait and try again before submitting another request."
            return render_template(
                "form.html",
                output_text=output_text,
                success_message="",
                excel_filename=None
            )

        # 🔒 Try to acquire lock first
        if not pipeline_lock.acquire(blocking=False):
            log_request(None, None, None, user_key, "web", "locked")
            output_text = "⚠️ Another request is currently running. Please wait a moment and try again."
            return render_template("form.html", output_text=output_text, success_message="", excel_filename=None)

        try:
            ticker = request.form.get("ticker").upper()
            if ticker not in VALID_TICKERS:
                log_request(ticker, None, None, user_key, "web", "denied")
                output_text = f"❌ Sorry, '{ticker}' is not a supported stock. This updater only works for US-listed and US-based companies that file 10-K's and 10-Q's with the SEC."
                return render_template("form.html", output_text=output_text, success_message="")
            
            year = int(request.form.get("year"))
            quarter = int(request.form.get("quarter"))
            full_year_mode = bool(request.form.get("full_year_mode"))
            debug_mode = bool(request.form.get("debug_mode"))
            excel_file = request.form.get("excel_file") or "Updater_EDGAR.xlsm"
            sheet_name = request.form.get("sheet_name") or "Raw_data"

            # ✅ Build full Excel file path
            excel_filename = f"{ticker}_{quarter}Q{str(year)[-2:]}_{excel_file}"
            updated_excel_file = os.path.join(EXPORT_DIR, excel_filename)

            log_dir = "pipeline_logs"
            log_filename = f"{ticker}_{quarter}Q{str(year)[-2:]}_{excel_file.replace('.xlsm', '')}_log.txt"
            log_path = os.path.join(log_dir, log_filename)

            # ✅ Check if file already exists
            if os.path.exists(updated_excel_file) and os.path.exists(log_path):
                log_request(ticker, year, quarter, user_key, "web", "cache_hit")
                success_message = f"✅ Updater file ready for {ticker} {quarter}Q{year}!"

                # Get the summary log
                with open(log_path, "r") as f:
                    output_text = f.read()

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

            log_usage(
                ticker=ticker,
                year=year,
                quarter=quarter,
                key=user_key,
                source="web",
                status="success"
            )

            # Get print outputs from the pipeline
            log_text = output_buffer.getvalue()
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
                summary_text = "\n".join(lines[start_index:end_index])

                #Show summary in the browser
                output_text = summary_text

                # Save only the summary
                log_dir = "pipeline_logs"
                os.makedirs(log_dir, exist_ok=True)

                log_filename = f"{ticker}_{quarter}Q{str(year)[-2:]}_{excel_file.replace('.xlsm', '')}_log.txt"
                log_path = os.path.join(log_dir, log_filename)

                with open(log_path, "w") as f:
                    f.write(summary_text)
  
                success_message = f"✅ Updater file completed for {ticker} {quarter}Q{year}!"
                log_request(ticker, year, quarter, user_key, "web", "success")

            else:
                summary_text = "⚠️ Processing did not complete. Kindly send an email to support@henrychien.com with the error message and we'll look into it."

                log_request(ticker, year, quarter, user_key, "web", "error")

                return render_template(
                    "form.html",
                    output_text=summary_text,
                    success_message="❌ This filing could not be completed successfully.",
                    excel_filename=None
                )

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
            log_path = log_error_json("WebUI", context, e)
            output_text = (
                "❌ This filing could not be processed.\n\n"
                f"Error details: {str(e)}\n"
                f"A full error log was saved to: {log_path}\n"
                "Please send an email to support@henrychien.com with the error message and we'll look into it."
            )

        finally:
            # 🔓 Always release the lock after processing
            pipeline_lock.release()

    return render_template("form.html", output_text=output_text, success_message=success_message, excel_filename=excel_filename)

@app.errorhandler(429)
def ratelimit_handler(e):
    return (
        "⚠️ Rate limit exceeded. Please wait and try again later.",
        429
    )

if __name__ == "__main__":
    app.run(port=5000)