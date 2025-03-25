from datetime import timedelta
import os
import time
import io
import logging
import zipfile
import pandas as pd
import streamlit as st

LOG_DIR = os.path.join("/tmp", "MyAppLogs")
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "comparison_log.txt")
logging.basicConfig(filename=log_file, level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logging.info("Streamlit app started.")

def prepare_file(uploaded_file):
    if not uploaded_file:
        return None
    file_bytes = uploaded_file.read()
    file_obj = io.BytesIO(file_bytes)
    file_obj.name = uploaded_file.name
    return file_obj

REQUIRED_COLUMNS_ISBN = ["ISBN13", "TITLE", "AUTHOR", "PUBLISHER", "STOCK", "CUR", "RRP", "DISCOUNT", "WEIGHT", "DIM1", "DIM2", "DIM3", "IMPRINT"]
REQUIRED_COLUMNS_EAN = ["EAN #", "TITLE", "AUTHOR", "PUBLISHER", "QTYAV", "CUR", "PRICE", "WGT OZS", "LENGTH", "WIDTH", "HEIGHT", "CD"]
REQUIRED_COLUMNS_IND = ["ISBN", "TITLE", "AUTHOR", "PUBLISHER", "STOCK", "CURRENCY", "PRICE", "COMPANY", "HANDLING"]

def clean_isbn(isbn):
    if pd.isna(isbn):
        return ""
    x = str(isbn).strip().replace("\u200b", "").replace("\xa0", "").replace("\ufeff", "")
    return x.zfill(13) if x.isnumeric() and len(x) < 13 else x

def detect_header(file_obj):
    file_obj.seek(0)
    ext = os.path.splitext(file_obj.name)[1].lower() if hasattr(file_obj, "name") else ".csv"
    if ext == ".csv":
        df = pd.read_csv(file_obj, dtype=str, nrows=10, header=None)
    else:
        df = pd.read_excel(file_obj, dtype=str, nrows=10, header=None)
    for i, row in df.iterrows():
        vals = row.astype(str).str.upper().str.replace(r"[^\w\s#]", "", regex=True).str.strip()
        if any("ISBN13" in v or "EAN" in v or "ISBN" in v for v in vals):
            return i
    return 0

def process_file(file_obj):
    filename = file_obj.name.lower() if hasattr(file_obj, "name") else ""
    hdr = detect_header(file_obj)
    file_obj.seek(0)
    if filename.endswith(".csv"):
        df = pd.read_csv(file_obj, encoding="ISO-8859-1", dtype=str, errors="replace", header=hdr)
    else:
        df = pd.read_excel(file_obj, dtype=str, header=hdr)
    df.columns = df.columns.str.strip().str.upper().str.replace(r"[^\w\s#]", "", regex=True).str.strip()
    key_column = next((col for col in df.columns if "ISBN13" in col.replace(" ", "") or "EAN#" in col.replace(" ", "") or "ISBN" in col.replace(" ", "")), None)
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
    if "ISBN13" in df.columns:
        required_columns = REQUIRED_COLUMNS_ISBN
    elif "EAN #" in df.columns or "EAN#" in df.columns:
        required_columns = REQUIRED_COLUMNS_EAN
    elif "ISBN" in df.columns:
        required_columns = REQUIRED_COLUMNS_IND
    else:
        raise KeyError("No matching column structure found.")
    if rem_obj:
        remove_set = extract_isbns(rem_obj)
        df = df[~df[key_column].isin(remove_set)]
    stock_column = next((col for col in df.columns if "STOCK" in col.replace(" ", "") or "QTYAV" in col.replace(" ", "")), None)
    if stock_column:
        df = df[df[stock_column].fillna(0).astype(float) != 0]
    df = df[[x for x in required_columns if x in df.columns]]
    if "CUR" in required_columns:
        df["CUR"] = cur
    elif "CURRENCY" in required_columns:
        df["CURRENCY"] = cur
    df = df.reindex(columns=required_columns, fill_value="")
    return df

def to_excel_bytes(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return output.getvalue()

st.title("SHOOLINCOMP")
st.write("Upload **2 comparison files** (CSV or Excel) and an optional **ISBN Removal File** (Excel).")
col1, col2 = st.columns(2)
with col1:
    uploaded_file1 = st.file_uploader("Upload First Comparison File", type=["csv", "xlsx"])
with col2:
    uploaded_file2 = st.file_uploader("Upload Second Comparison File", type=["csv", "xlsx"])
uploaded_removal = st.file_uploader("Upload ISBN Removal File (Optional)", type=["xlsx"])
currency = st.radio("Select Currency", options=["USD", "GBP"], index=0)

if st.button("Start Cleaning & Comparison"):
    if not uploaded_file1 or not uploaded_file2:
        st.error("Please upload both comparison files.")
    else:
        start_time = time.time()
        with st.spinner("Processing..."):
            file1 = prepare_file(uploaded_file1)
            file2 = prepare_file(uploaded_file2)
            rem_file = prepare_file(uploaded_removal) if uploaded_removal else None
            d1 = clean_file(file1, currency, rem_file)
            d2 = clean_file(file2, currency, rem_file)
            key1, key2 = d1.columns[0], d2.columns[0]
            new_items = d2[~d2[key2].isin(d1[key1])]
            inactive_items = d1[~d1[key1].isin(d2[key2])]
            elapsed = round(time.time() - start_time, 2)
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_STORED) as zipf:
                zipf.writestr("cleaned/Cleaned_File1.xlsx", to_excel_bytes(d1))
                zipf.writestr("cleaned/Cleaned_File2.xlsx", to_excel_bytes(d2))
                zipf.writestr("comparison/New_Items.xlsx", to_excel_bytes(new_items))
                zipf.writestr("comparison/Inactive_Items.xlsx", to_excel_bytes(inactive_items))
            zip_buffer.seek(0)
            st.success(f"Processing completed in {elapsed} seconds.")
            st.download_button("📥 Download All Files (ZIP)", zip_buffer.getvalue(), file_name="Comparison_Output.zip", mime="application/zip")
