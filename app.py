#!/usr/bin/env python
# coding: utf-8

# In[ ]:


from flask import Flask, request, jsonify
from edgar_pipeline import run_edgar_pipeline

app = Flask(__name__)

@app.route('/run_pipeline', methods=['POST'])
def run_pipeline():
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

if __name__ == "__main__":
    app.run(port=5000)


# In[ ]:




