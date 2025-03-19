import os
import time
import io
import logging
import zipfile
import pandas as pd
import streamlit as st

# ---- Setup Logging ---- #
LOG_DIR = os.path.join(os.path.expanduser("~"), "Documents", "MyAppLogs")
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "comparison_log.txt")

logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logging.info("Streamlit app started.")

# ---- Required Columns ---- #
REQUIRED_COLUMNS_ISBN = [
    "ISBN13", "TITLE", "AUTHOR", "DISCOUNT", "STOCK", "CUR",
    "DIM1", "DIM2", "DIM3", "WEIGHT", "PUBLISHER", "IMPRINT"
]
REQUIRED_COLUMNS_EAN = [
    "EAN", "TITLE", "AUTHOR", "PUBLISHER", "QTYAV", "CUR", "PRICE", 
    "WGT OZS", "LENGTH", "WIDTH", "HEIGHT", "CD"
]

# ---- Helper Functions ---- #
def clean_isbn(isbn):
    if pd.isna(isbn):
        return ""
    x = str(isbn).strip().replace("\u200b", "").replace("\xa0", "").replace("\ufeff", "")
    return x.zfill(13) if x.isnumeric() and len(x) < 13 else x

def detect_header(file_obj):
    file_obj.seek(0)
    filename = file_obj.name.lower() if hasattr(file_obj, "name") else ""
    reader = pd.read_csv if filename.endswith(".csv") else pd.read_excel
    df = reader(file_obj, dtype=str, nrows=20, header=None)
    
    for i, row in df.iterrows():
        vals = row.astype(str).str.upper().str.replace(r"[^\w\s]", "", regex=True).str.strip()
        if any("ISBN13" in v.replace(" ", "") or "EAN" in v.replace(" ", "") for v in vals):
            return i
    raise KeyError("No valid header row found.")

def process_file(file_obj):
    filename = file_obj.name.lower() if hasattr(file_obj, "name") else ""
    hdr = detect_header(file_obj)
    file_obj.seek(0)
    
    if filename.endswith(".csv"):
        df = pd.read_csv(file_obj, encoding="ISO-8859-1", dtype=str, errors="replace", header=hdr)
    else:
        df = pd.read_excel(file_obj, dtype=str, header=hdr)

    df.columns = df.columns.str.strip().str.upper().str.replace(r"[^\w\s]", "", regex=True).str.strip()
    
    key_column = next((col for col in df.columns if "ISBN13" in col.replace(" ", "") or "EAN" in col.replace(" ", "")), None)
    if not key_column:
        raise KeyError("No ISBN/EAN column found.")
    
    df[key_column] = df[key_column].apply(clean_isbn)
    
    stock_column = next((col for col in df.columns if "STOCK" in col.replace(" ", "") or "QTYAV" in col.replace(" ", "")), None)
    if stock_column:
        df[stock_column] = pd.to_numeric(df[stock_column], errors="coerce")

    return df, key_column

def extract_isbns(file_obj):
    try:
        file_obj.seek(0)
        df = pd.read_excel(file_obj, header=None, dtype=str)
        return {clean_isbn(v) for v in df.values.flatten() if str(v).isnumeric()}
    except Exception as e:
        logging.error(f"Error reading ISBN removal file: {e}")
        return set()

def clean_file(file_obj, cur, rem_obj=None):
    df, key_column = process_file(file_obj)
    required_columns = REQUIRED_COLUMNS_ISBN if "ISBN13" in df.columns else REQUIRED_COLUMNS_EAN

    if rem_obj:
        remove_set = extract_isbns(rem_obj)
        df = df[~df[key_column].isin(remove_set)]
    
    df = df[[x for x in required_columns if x in df.columns]]
    
    if "CUR" not in df.columns:
        df["CUR"] = ""
    df["CUR"] = cur

    df = df.reindex(columns=required_columns, fill_value="")

    return df

def to_excel_bytes(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return output.getvalue()

def prepare_file(uploaded_file):
    file_bytes = uploaded_file.read()
    file_obj = io.BytesIO(file_bytes)
    file_obj.name = uploaded_file.name
    return file_obj

# ---- Streamlit UI ---- #
st.title("File Cleaner & Comparator")
st.write("Upload **2 comparison files** (CSV or Excel) and an optional **ISBN Removal File** (Excel).")

col1, col2 = st.columns(2)
with col1:
    uploaded_file1 = st.file_uploader("Upload First Comparison File", type=["csv", "xlsx"])
with col2:
    uploaded_file2 = st.file_uploader("Upload Second Comparison File", type=["csv", "xlsx"])

uploaded_removal = st.file_uploader("Upload ISBN Removal File (Optional)", type=["xlsx"])
currency = st.radio("Select Currency", options=["USD", "GBP"], index=0)

download_placeholder = st.empty()  # Placeholder for the download button

if st.button("Start Cleaning & Comparison"):
    if not uploaded_file1 or not uploaded_file2:
        st.error("Please upload both comparison files.")
    else:
        start_time = time.time()
        progress_bar = st.progress(0)
        status_text = st.empty()

        try:
            file1 = prepare_file(uploaded_file1)
            file2 = prepare_file(uploaded_file2)
            rem_file = prepare_file(uploaded_removal) if uploaded_removal else None

            progress_bar.progress(10)
            status_text.text("Cleaning first file...")
            d1 = clean_file(file1, currency, rem_file)

            progress_bar.progress(30)
            status_text.text("Cleaning second file...")
            d2 = clean_file(file2, currency, rem_file)

            progress_bar.progress(50)
            status_text.text("Comparing files...")
            key1, key2 = d1.columns[0], d2.columns[0]
            new_items = d2[~d2[key2].isin(d1[key1])]
            inactive_items = d1[~d1[key1].isin(d2[key2])]

            progress_bar.progress(80)
            elapsed = round(time.time() - start_time, 2)
            status_text.text(f"✅ Processing completed in {elapsed} seconds.")

            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
                zipf.writestr("cleaned/Cleaned_File1.xlsx", to_excel_bytes(d1))
                zipf.writestr("cleaned/Cleaned_File2.xlsx", to_excel_bytes(d2))
                zipf.writestr("comparison/New_Items.xlsx", to_excel_bytes(new_items))
                zipf.writestr("comparison/Inactive_Items.xlsx", to_excel_bytes(inactive_items))
            
            zip_buffer.seek(0)
            zip_data = zip_buffer.getvalue()

            st.success(f"Processing completed in {elapsed} seconds.")
            download_placeholder.download_button("📥 Download All Files (ZIP)", zip_data, file_name="Comparison_Output.zip", mime="application/zip")
            progress_bar.progress(100)

        except Exception as e:
            st.error(f"An error occurred: {e}")
            logging.error(f"Processing error: {e}")
