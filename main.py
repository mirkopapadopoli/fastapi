# from fastapi import FastAPI

# app = FastAPI()

# @app.get("/")
# async def root():
#     return {"greeting": "Hello, World!", "message": "Welcome to FastAPI!"}

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import tempfile
import os
import csv
import io
from datetime import datetime
import base64

# Import your existing extraction logic
import pdfplumber
import re
from collections import defaultdict

app = FastAPI(
    title="BeeBus Fatture Extractor",
    description="AI-powered PDF extraction per rifornimenti carburante",
    version="1.0.0",
    docs_url="/docs",  # Auto-documentation per demo cliente
    redoc_url="/redoc"
)

# CORS per frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In produzione: domini specifici BeeBus
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models per API documentation
class ExtractionResult(BaseModel):
    status: str
    filename: str
    timestamp: str
    records_count: int
    total_amount: float
    data: List[dict]

class BatchResult(BaseModel):
    status: str
    processed_files: int
    total_records: int
    results: List[ExtractionResult]

# Health check per Railway monitoring
@app.get("/")
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "beebus-extractor",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }

# IL TUO SCRIPT ORIGINALE COME FUNZIONI
def normalizza_numero(s: str):
    """Il tuo codice esistente"""
    if not s:
        return ""
    s = s.replace(".", "").replace(",", ".")
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s

def trova_transazione(line):
    """Il tuo pattern regex esistente"""
    pattern = (
        r"^(\d{2}/\d{2}/\d{2})\s+"     # Data
        r"(\d{2}:\d{2})\s+"            # Ora
        r"(\d{8})\s+"                  # Numero scontrino
        r"(\d{5})\s+"                  # Codice PV
        r"(.+?)\s+"                    # Località
        r"(\d{1,3}(?:\.\d{3})*|1)\s+"  # Chilometraggio
        r"0000\s+"                     # Codice fisso
        r"GASOLIO(?:\s+SELF)?\s+"      # GASOLIO con SELF opzionale
        r"([\d,]+)"                    # Litri
    )
    return re.search(pattern, line)

def estrai_importo_finale(line):
    """Il tuo codice esistente"""
    importi = re.findall(r"\d+,\d+", line)
    return normalizza_numero(importi[-1]) if importi else ""

def estrai_targa(line):
    """Il tuo codice esistente"""
    pattern = r"TARGA\s+([A-Z]{2}[0-9]{3}[A-Z]{2})"
    m = re.search(pattern, line)
    return m.group(1) if m else None

def valida_chilometraggio(raw):
    """Il tuo codice esistente"""
    try:
        km = int(raw.replace('.', ''))
        if km > 10_000_000:
            return 0
        return km
    except Exception:
        return 0

def determina_tipo_gasolio(line):
    """Il tuo codice esistente"""
    if "GASOLIO SELF" in line:
        return "esterno"
    elif "GASOLIO" in line:
        return "esterno"
    return "esterno"

def process_pdf_content(pdf_content: bytes, filename: str) -> dict:
    """
    La tua logica di estrazione PDF adattata per FastAPI
    """
    fieldnames = [
        "Targa", "Data_Rifornimento", "Ora_Rifornimento", "Chilometraggio",
        "Litri", "Importo_Totale", "Fornitore", "Tipo_Rifornimento",
        "Numero_Scontrino", "Localita"
    ]
    
    records_finali = []
    transazioni_in_attesa = []
    visti = set()
    
    # Salva temporaneamente il PDF
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_file:
        temp_file.write(pdf_content)
        temp_pdf_path = temp_file.name
    
    try:
        with pdfplumber.open(temp_pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                words = page.extract_words(x_tolerance=3, y_tolerance=3, keep_blank_chars=False, use_text_flow=True)
                righe = defaultdict(list)
                
                for w in words:
                    righe[round(w["top"])].append(w["text"])
                
                for top in sorted(righe.keys()):
                    line = " ".join(righe[top]).strip()
                    if not line:
                        continue

                    # Cerca Totale carta
                    targa = estrai_targa(line)
                    if targa:
                        for transazione in transazioni_in_attesa:
                            transazione["Targa"] = targa
                            key = (transazione["Data_Rifornimento"], transazione["Ora_Rifornimento"], transazione["Numero_Scontrino"])
                            if key not in visti:
                                visti.add(key)
                                records_finali.append(transazione)
                        
                        transazioni_in_attesa = []
                        continue

                    # Accumula transazioni
                    match_txn = trova_transazione(line)
                    if match_txn:
                        try:
                            data = match_txn.group(1)
                            ora = match_txn.group(2)
                            numero_scontrino = match_txn.group(3)
                            codice_pv = match_txn.group(4)
                            localita_raw = match_txn.group(5)
                            chilometraggio_raw = match_txn.group(6)
                            litri_raw = match_txn.group(7)
                            
                            localita = localita_raw.strip().rstrip(',')
                            chilometraggio = valida_chilometraggio(chilometraggio_raw)
                            litri = normalizza_numero(litri_raw)
                            importo_totale = estrai_importo_finale(line)
                            tipo_rifornimento = determina_tipo_gasolio(line)
                            
                            transazione_temp = {
                                "Targa": "",
                                "Data_Rifornimento": data,
                                "Ora_Rifornimento": ora,
                                "Chilometraggio": chilometraggio,
                                "Litri": litri,
                                "Importo_Totale": importo_totale,
                                "Fornitore": "IP",
                                "Tipo_Rifornimento": tipo_rifornimento,
                                "Numero_Scontrino": numero_scontrino,
                                "Localita": localita
                            }
                            
                            transazioni_in_attesa.append(transazione_temp)
                            
                        except Exception as e:
                            continue

        # Gestisci transazioni rimaste
        if transazioni_in_attesa:
            for transazione in transazioni_in_attesa:
                transazione["Targa"] = "SCONOSCIUTA"
                key = (transazione["Data_Rifornimento"], transazione["Ora_Rifornimento"], transazione["Numero_Scontrino"])
                if key not in visti:
                    visti.add(key)
                    records_finali.append(transazione)

        total_amount = sum(float(r.get('Importo_Totale', 0)) for r in records_finali if r.get('Importo_Totale'))
        
        return {
            "status": "success",
            "filename": filename,
            "timestamp": datetime.now().isoformat(),
            "records_count": len(records_finali),
            "total_amount": total_amount,
            "data": records_finali
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore processing PDF: {str(e)}")
        
    finally:
        os.unlink(temp_pdf_path)

# API ENDPOINTS

@app.post("/extract", response_model=ExtractionResult)
async def extract_single_pdf(file: UploadFile = File(...)):
    """
    Estrae dati da singolo PDF fattura carburante
    
    - **file**: PDF file della fattura (max 50MB)
    """
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File deve essere PDF")
    
    if file.size > 50 * 1024 * 1024:  # 50MB limit
        raise HTTPException(status_code=400, detail="File troppo grande (max 50MB)")
    
    content = await file.read()
    result = process_pdf_content(content, file.filename)
    
    return result

@app.post("/extract-batch", response_model=BatchResult)
async def extract_multiple_pdfs(files: List[UploadFile] = File(...)):
    """
    Estrae dati da multipli PDF fatture
    
    - **files**: Lista di PDF files (max 10 files)
    """
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="Max 10 files per batch")
    
    results = []
    total_records = 0
    
    for file in files:
        if file.filename.endswith('.pdf'):
            content = await file.read()
            result = process_pdf_content(content, file.filename)
            results.append(result)
            total_records += result["records_count"]
    
    return {
        "status": "success",
        "processed_files": len(results),
        "total_records": total_records,
        "results": results
    }

@app.post("/extract-csv")
async def extract_and_download_csv(files: List[UploadFile] = File(...)):
    """
    Estrae dati e restituisce CSV per download diretto
    """
    all_records = []
    
    for file in files:
        if file.filename.endswith('.pdf'):
            content = await file.read()
            result = process_pdf_content(content, file.filename)
            all_records.extend(result["data"])
    
    # Genera CSV
    output = io.StringIO()
    fieldnames = [
        "Targa", "Data_Rifornimento", "Ora_Rifornimento", "Chilometraggio",
        "Litri", "Importo_Totale", "Fornitore", "Tipo_Rifornimento",
        "Numero_Scontrino", "Localita"
    ]
    
    writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=";")
    writer.writeheader()
    writer.writerows(all_records)
    
    # Return CSV as download
    csv_content = output.getvalue()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"beebus_rifornimenti_{timestamp}.csv"
    
    return JSONResponse(
        content={"csv_data": csv_content, "filename": filename},
        headers={"Content-Type": "application/json"}
    )

# Per development locale
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)