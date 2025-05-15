from flask import Flask, request, jsonify, render_template, send_from_directory
from edgar_pipeline import run_edgar_pipeline
import io
from contextlib import redirect_stdout
import os
import json
import traceback
from datetime import datetime

# === Load valid tickers ===
VALID_TICKERS = set()
with open("valid_tickers.csv", "r") as f:
    for line in f.readlines()[1:]:  # Skip header
        VALID_TICKERS.add(line.strip().upper())

# === Define export folder ===
EXPORT_DIR = "exports"
os.makedirs(EXPORT_DIR, exist_ok=True)

app = Flask(__name__)

# === Logging config ===
LOG_DIR = "error_logs"
os.makedirs(LOG_DIR, exist_ok=True)

def log_error_json(source, context, exc):
    """Save error context and traceback to a JSON file."""
    error_info = {
        "timestamp": datetime.utcnow().isoformat(),
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


# === File download route ===
@app.route("/download/<filename>")
def download_file(filename):
    return send_from_directory(EXPORT_DIR, filename, as_attachment=True)

# === API endpoint ===
@app.route("/run_pipeline", methods=["POST"])
def run_pipeline():
    if request.method == 'POST':
        data = request.json
        try:
            ticker = data['ticker'].upper()
            if ticker not in VALID_TICKERS:
                return jsonify({"status": "error", "message": f"Sorry, '{ticker}' is not a supported stock. This updater only works for US-listed and US-based companies that file 10-K's and 10-Q's with the SEC."})
            year = int(data['year'])
            quarter = int(data['quarter'])
            full_year_mode = data.get('full_year_mode', False)
            debug_mode = data.get('debug_mode', False)
            excel_file = data.get('excel_file', "Updater_EDGAR.xlsm")
            sheet_name = data.get('sheet_name', "Raw_data")

            run_edgar_pipeline(
                ticker,
                year,
                quarter,
                full_year_mode,
                debug_mode,
                excel_file,
                sheet_name
            )

            return jsonify({"status": "success", "message": f"Pipeline completed for {ticker} {quarter}Q{year}"})

        except Exception as e:
            log_error_json("API", data, e)
            return jsonify({"status": "error", "message": str(e)})

# === Web UI form route ===
@app.route("/", methods=["GET", "POST"])
def web_ui():
    output_text = ""
    success_message = ""
    excel_filename = None

    if request.method == "POST":
        try:
            ticker = request.form.get("ticker").upper()
            if ticker not in VALID_TICKERS:
                output_text = f"❌ Sorry, '{ticker}' is not a supported stock. This updater only works for US-listed and US-based companies that file 10-K's and 10-Q's with the SEC."
                return render_template("form.html", output_text=output_text, success_message="")
            year = int(request.form.get("year"))
            quarter = int(request.form.get("quarter"))
            full_year_mode = bool(request.form.get("full_year_mode"))
            debug_mode = bool(request.form.get("debug_mode"))
            excel_file = request.form.get("excel_file") or "Updater_EDGAR.xlsm"
            sheet_name = request.form.get("sheet_name") or "Raw_data"

            # ✅ Build full Excel file path
            excel_filename = f"Updater_EDGAR.xlsm"
            excel_file = os.path.join(EXPORT_DIR, excel_filename)

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

            output_text = output_buffer.getvalue()
            lines = output_text.splitlines()

            # Find markers for the section you want
            start_index = None
            end_index = None

            for i, line in enumerate(lines):
                if "📄 Export summary:" in line and start_index is None:
                    start_index = i
                if "⏱️ Total processing time" in line:
                    end_index = i + 1  # include this line too

            # Extract just the desired section
            if start_index is not None and end_index is not None:
                output_text = "\n".join(lines[start_index:end_index])

            else:
                output_text = "⚠️ Processing did not complete. Kindly send an email to support@henrychien.com with the error message and we'll look into it."
                
            success_message = f"✅ Updater file completed for {ticker} {quarter}Q{year}!"

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

    return render_template("form.html", output_text=output_text, success_message=success_message, excel_filename=excel_filename)

if __name__ == "__main__":
    app.run(port=5000)