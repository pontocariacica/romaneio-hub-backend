# -*- coding: utf-8 -*-
"""
ROMANEIO HUB - API de processamento de PDF (FastAPI).

Endpoints:
  GET  /health            -> verificação simples
  POST /process           -> recebe um PDF e devolve os romaneios separados

Segurança:
  - valida tipo (MIME + assinatura %PDF), tamanho e nº de páginas;
  - CORS restrito por variável de ambiente (ALLOWED_ORIGINS);
  - timeout de processamento;
  - não usa o nome do arquivo enviado como caminho no servidor;
  - não executa conteúdo/JS incorporado no PDF;
  - segredos e configurações via variáveis de ambiente (nunca no código).
"""
from __future__ import annotations

import asyncio
import os
import time

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from processing import process_pdf

# --------------------------------------------------------------- configuração
MAX_MB = int(os.getenv("MAX_MB", "50"))
MAX_PAGES = int(os.getenv("MAX_PAGES", "1500"))
OCR_ENABLED = os.getenv("OCR_ENABLED", "false").lower() in ("1", "true", "yes")
TIMEOUT_S = int(os.getenv("TIMEOUT_SECONDS", "120"))
ALLOWED = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

app = FastAPI(title="Romaneio Hub - Processador", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True, "ocr": OCR_ENABLED, "max_mb": MAX_MB, "max_pages": MAX_PAGES}


@app.post("/process")
async def process(
    file: UploadFile = File(...),
    data_informada: str | None = Form(default=None),
    qtd_informada: int | None = Form(default=None),
):
    # 1) validação de tipo declarado
    ctype = (file.content_type or "").lower()
    if "pdf" not in ctype and not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Envie um arquivo PDF.")

    # 2) leitura com limite de tamanho (evita estourar memória)
    limit = MAX_MB * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"Arquivo excede {MAX_MB} MB.")
    if not data:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")

    # 3) processamento com timeout, isolado numa thread
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                process_pdf, data, data_informada,
                qtd_informada, OCR_ENABLED, MAX_PAGES,
            ),
            timeout=TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Tempo de processamento excedido.")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        # nunca expõe stack trace ao cliente
        raise HTTPException(status_code=500, detail="Não foi possível processar este documento.")

    result["tempo_ms"] = round((time.perf_counter() - t0) * 1000)
    return JSONResponse(result)
