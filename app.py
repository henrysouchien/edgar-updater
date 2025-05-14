from flask import Flask, request, jsonify, render_template
from edgar_pipeline import run_edgar_pipeline
import io
from contextlib import redirect_stdout

app = Flask(__name__)

# === API endpoint ===
@app.route("/run_pipeline", methods=["POST"])
def run_pipeline():
    if request.method == 'POST':
        data = request.json
        try:
            ticker = data['ticker']
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
            return jsonify({"status": "error", "message": str(e)})

# === Web UI form route ===
@app.route("/", methods=["GET", "POST"])
def web_ui():
    output_text = ""
    success_message = ""

    if request.method == "POST":
        try:
            ticker = request.form.get("ticker")
            year = int(request.form.get("year"))
            quarter = int(request.form.get("quarter"))
            full_year_mode = bool(request.form.get("full_year_mode"))
            debug_mode = bool(request.form.get("debug_mode"))
            excel_file = request.form.get("excel_file") or "Updater_EDGAR.xlsm"
            sheet_name = request.form.get("sheet_name") or "Raw_data"

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
            success_message = f"✅ Updater file completed for {ticker} {quarter}Q{year}"

        except Exception as e:
            output_text = f"❌ Error: {str(e)}"

    return render_template("form.html", output_text=output_text, success_message=success_message)

if __name__ == "__main__":
    app.run(port=5000)