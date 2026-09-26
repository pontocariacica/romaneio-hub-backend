# -*- coding: utf-8 -*-
"""
ROMANEIO HUB - núcleo de processamento de PDF.

Responsável por:
  - ler o PDF de forma robusta (PyMuPDF / fitz);
  - extrair o texto por página em LINHAS (usado para identificação);
  - detectar onde cada romaneio começa/termina;
  - separar em PDFs individuais (base64);
  - extrair campos (número, viagem, data, placa, transportadora, motorista).

Não executa JavaScript nem conteúdo incorporado do PDF.
As regras de identificação ficam concentradas aqui, propositalmente,
para serem calibradas conforme o layout real dos romaneios.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

import fitz  # PyMuPDF

# ------------------------------------------------------------------ regras
KW_RE = re.compile(r"rom[aâ]neio", re.IGNORECASE)
NUM_RE = re.compile(r"rom[aâ]neio(?:\s+de\s+\w+)?[\s:.\-nº°]*(\d{3,10})", re.IGNORECASE)
VIAGEM_RE = re.compile(r"viagem[\s:.\-nº°]*(\d{2,12})", re.IGNORECASE)
FROTA_RE = re.compile(r"frota[\s:.\-nº°]*(\d{2,8})", re.IGNORECASE)
CARGA_RE = re.compile(r"n[º°#]?\s*(?:da\s+)?carga[\s:.\-nº°#]*(\d{2,10})", re.IGNORECASE)
DATA_RE = re.compile(r"\b(\d{2}/\d{2}/\d{2,4})\b")
PLACA_RE = re.compile(r"\b([A-Z]{3}[-\s]?\d[A-Z0-9]\d{2})\b")
TRANSP_RE = re.compile(r"transportadora[:\s]+([A-Z0-9À-Ú&.\- ]{3,45})", re.IGNORECASE)
MOTOR_RE = re.compile(r"motorista[\s:.\-]*([A-Za-zÀ-ú][A-Za-zÀ-ú.\- ]{3,50})", re.IGNORECASE)
MOTOR_STOP_RE = re.compile(r"\s+(Cavalo|Carreta|Data|Rampa|Hora|Placa|N[º°#])\b", re.IGNORECASE)
ORIGEM_RE = re.compile(r"origem[:\s]+([A-Z0-9À-Ú][A-Z0-9À-Úa-zà-ú,.\-/ ]{2,45})", re.IGNORECASE)
DEST_RE = re.compile(r"destino[:\s]+([A-Z0-9À-Ú][A-Z0-9À-Úa-zà-ú,.\-/ ]{2,45})", re.IGNORECASE)


def _clean_field(m: Optional[re.Match]) -> Optional[str]:
    if not m:
        return None
    v = re.sub(r"\s+", " ", m.group(1)).strip()
    v = re.sub(r"\s+(DATA|PLACA|MOTORISTA|DESTINO|ORIGEM|N[º°]).*$", "", v, flags=re.IGNORECASE).strip()
    return v or None


@dataclass
class PageInfo:
    index: int          # 1-based
    lines: List[str]
    text: str
    top_text: str
    empty: bool
    num: Optional[str]
    num_top: Optional[str]
    has_kw: bool
    kw_top: bool
    ocr: bool = False


@dataclass
class Romaneio:
    ordem: int
    pagina_inicio: int
    pagina_fim: int
    confianca: int
    numero: Optional[str] = None
    viagem: Optional[str] = None
    frota: Optional[str] = None
    carga: Optional[str] = None
    data: Optional[str] = None
    placa: Optional[str] = None
    transportadora: Optional[str] = None
    motorista: Optional[str] = None
    origem: Optional[str] = None
    destino: Optional[str] = None
    ocr: bool = False
    nome_arquivo: str = ""
    linhas: List[str] = field(default_factory=list)   # todas as linhas do romaneio (frente + verso)
    base64: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ordem": self.ordem,
            "numero": self.numero,
            "viagem": self.viagem,
            "frota": self.frota,
            "carga": self.carga,
            "data": self.data,
            "placa": self.placa,
            "transportadora": self.transportadora,
            "motorista": self.motorista,
            "origem": self.origem,
            "destino": self.destino,
            "pagina_inicio": self.pagina_inicio,
            "pagina_fim": self.pagina_fim,
            "confianca": self.confianca,
            "ocr": self.ocr,
            "nome_arquivo": self.nome_arquivo,
            "linhas": self.linhas,
            "base64": self.base64,
        }


# ------------------------------------------------------------------ leitura
def _page_lines(page: "fitz.Page") -> List[str]:
    """Reconstrói as linhas de texto respeitando a posição (y depois x)."""
    d = page.get_text("dict")
    rows = []
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            y = round(spans[0]["bbox"][1])
            txt = " ".join(s.get("text", "") for s in spans)
            txt = re.sub(r"\s+", " ", txt).strip()
            if txt:
                rows.append((y, spans[0]["bbox"][0], txt))
    rows.sort(key=lambda r: (r[0], r[1]))
    return [t for _, _, t in rows]


def _analyze_page(page: "fitz.Page", idx: int, ocr_enabled: bool) -> PageInfo:
    lines = _page_lines(page)
    text = re.sub(r"\s+", " ", " ".join(lines)).strip()
    ocr_used = False

    # OCR só nas FRENTES (páginas ímpares 1-based): é onde ficam frota,
    # romaneio, motorista e data. O verso não precisa de OCR — isso corta o
    # trabalho pela metade e evita o estouro de tempo no servidor grátis.
    if len(text) < 12 and ocr_enabled and (idx % 2 == 1):
        ocr_text = _ocr_page(page)
        if ocr_text:
            lines = [l for l in ocr_text.splitlines() if l.strip()]
            text = re.sub(r"\s+", " ", ocr_text).strip()
            ocr_used = True

    top_cut = page.rect.height * 0.28  # 28% do topo
    top_lines = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if spans and spans[0]["bbox"][1] <= top_cut:
                top_lines.append(" ".join(s.get("text", "") for s in spans))
    top_text = re.sub(r"\s+", " ", " ".join(top_lines)).strip() or " ".join(lines[:3])

    num_m = NUM_RE.search(text)
    num_top = NUM_RE.search(top_text)
    return PageInfo(
        index=idx,
        lines=lines,
        text=text,
        top_text=top_text,
        empty=len(text) < 12,
        num=num_m.group(1) if num_m else None,
        num_top=num_top.group(1) if num_top else None,
        has_kw=bool(KW_RE.search(text)),
        kw_top=bool(KW_RE.search(top_text)) or bool(num_top),
        ocr=ocr_used,
    )


import os as _os

# Ajustes de desempenho do OCR (calibráveis por variável de ambiente).
# OCR pesado é o que trava o plano grátis; por isso lemos só a faixa de cima
# da página (onde ficam frota/romaneio/motorista/data), num único passe.
_OCR_TOP_FRACTION = float(_os.getenv("OCR_TOP_FRACTION", "0.62"))  # % da altura, do topo
_OCR_ZOOM = float(_os.getenv("OCR_ZOOM", "2.0"))                   # resolução do recorte
_OCR_CONFIG = _os.getenv("OCR_CONFIG", "--oem 1 --psm 6")          # tesseract rápido
_OCR_LANG_CACHE = "__unset__"


def _ocr_lang() -> Optional[str]:
    """Descobre UMA vez o melhor idioma instalado (por > eng > padrão)."""
    global _OCR_LANG_CACHE
    if _OCR_LANG_CACHE == "__unset__":
        lang = None
        try:
            import pytesseract  # noqa
            avail = set(pytesseract.get_languages(config=""))
            if "por" in avail:
                lang = "por"
            elif "eng" in avail:
                lang = "eng"
        except Exception:
            lang = None
        _OCR_LANG_CACHE = lang
    return _OCR_LANG_CACHE


def _ocr_page(page: "fitz.Page") -> str:
    """
    OCR rápido: rasteriza APENAS a faixa superior da página e roda o tesseract
    num único passe (idioma detectado uma vez, configuração rápida). Mantém o
    custo de CPU baixo o suficiente para o servidor grátis responder a tempo.
    """
    try:
        import pytesseract  # noqa
        from PIL import Image  # noqa
        import io
        r = page.rect
        frac = min(max(_OCR_TOP_FRACTION, 0.2), 1.0)
        clip = fitz.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * frac)
        pix = page.get_pixmap(matrix=fitz.Matrix(_OCR_ZOOM, _OCR_ZOOM), clip=clip)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    except Exception:
        return ""
    lang = _ocr_lang()
    try:
        if lang:
            return pytesseract.image_to_string(img, lang=lang, config=_OCR_CONFIG) or ""
        return pytesseract.image_to_string(img, config=_OCR_CONFIG) or ""
    except Exception:
        try:
            return pytesseract.image_to_string(img) or ""
        except Exception:
            return ""


# ------------------------------------------------------------------ detecção
def _is_frente(p: PageInfo) -> bool:
    """Uma FRENTE é a página que traz a linha de identificação (frota/viagem)."""
    return bool(VIAGEM_RE.search(p.text) or FROTA_RE.search(p.text))


def _detect_segments(pages: List[PageInfo]) -> List[Dict[str, Any]]:
    """
    Cada romaneio = FRENTE + VERSO. Agrupa SEMPRE de 2 em 2 (frente+verso),
    formando um PDF por par. Frota e viagem são lidas do texto só para identificar.
    """
    segs: List[Dict[str, Any]] = []
    i = 0
    while i < len(pages):
        a = pages[i]
        b = pages[i + 1] if i + 1 < len(pages) else None
        segs.append({
            "start": a.index, "end": (b.index if b else a.index),
            "texts": [a.text] + ([b.text] if b else []),
            "lines": list(a.lines) + (list(b.lines) if b else []),
            "ocr": a.ocr or (b.ocr if b else False),
            "any_empty": a.empty or (b.empty if b else False),
        })
        i += 2
    return segs


def _motorista(t: str) -> Optional[str]:
    m = MOTOR_RE.search(t)
    if not m:
        return None
    v = m.group(1)
    v = MOTOR_STOP_RE.split(v)[0]
    v = re.sub(r"\s+", " ", v).strip()
    return v or None


def _build_romaneio(seg: Dict[str, Any], ordem: int) -> Romaneio:
    t = " ".join(seg["texts"])
    viagem = VIAGEM_RE.search(t).group(1) if VIAGEM_RE.search(t) else None
    frota = FROTA_RE.search(t).group(1) if FROTA_RE.search(t) else None
    numero = NUM_RE.search(t).group(1) if NUM_RE.search(t) else None
    carga = CARGA_RE.search(t).group(1) if CARGA_RE.search(t) else None
    data = DATA_RE.search(t).group(1) if DATA_RE.search(t) else None
    placa_m = PLACA_RE.search(t)
    placa = placa_m.group(1).replace(" ", "").upper() if placa_m else None

    conf = 98 if (frota and (numero or viagem)) else 90 if (frota or numero or viagem) else 55
    if seg["ocr"]:
        conf = round(conf * 0.9)

    partes = []
    if frota:
        partes.append("FROTA" + frota)
    if numero:
        partes.append(numero)
    elif viagem:
        partes.append("VIAGEM" + viagem)
    nome = ("ROMANEIO_" + "_".join(partes) + ".pdf") if partes else f"ROMANEIO_SEM_IDENTIFICACAO_{ordem:03d}.pdf"

    return Romaneio(
        ordem=ordem, pagina_inicio=seg["start"], pagina_fim=seg["end"], confianca=conf,
        numero=numero, viagem=viagem, frota=frota, carga=carga, data=data, placa=placa,
        transportadora=_clean_field(TRANSP_RE.search(t)),
        motorista=_motorista(t),
        origem=_clean_field(ORIGEM_RE.search(t)),
        destino=_clean_field(DEST_RE.search(t)),
        ocr=seg["ocr"], nome_arquivo=nome, linhas=seg["lines"],
    )


# ------------------------------------------------------------------ split
def _split(src: "fitz.Document", rom: Romaneio) -> None:
    out = fitz.open()
    out.insert_pdf(src, from_page=rom.pagina_inicio - 1, to_page=rom.pagina_fim - 1)
    rom.base64 = base64.b64encode(out.tobytes()).decode("ascii")
    out.close()


# ------------------------------------------------------------------ API pública
def process_pdf(
    data: bytes,
    data_informada: Optional[str] = None,
    qtd_informada: Optional[int] = None,
    ocr_enabled: bool = False,
    max_pages: int = 1500,
) -> Dict[str, Any]:
    if data[:5] != b"%PDF-":
        raise ValueError("Arquivo não é um PDF válido.")

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if doc.page_count > max_pages:
            raise ValueError(f"PDF com {doc.page_count} páginas excede o limite de {max_pages}.")

        pages = [_analyze_page(doc[i], i + 1, ocr_enabled) for i in range(doc.page_count)]
        segments = _detect_segments(pages)
        romaneios = [_build_romaneio(s, i + 1) for i, s in enumerate(segments)]

        # aplica a data informada onde não foi possível ler
        if data_informada:
            for r in romaneios:
                if not r.data:
                    r.data = data_informada
                    if r.numero:
                        r.nome_arquivo = f"ROMANEIO_{r.numero}_{data_informada.replace('/', '-')}.pdf"

        for r in romaneios:
            _split(doc, r)

        total_class = sum(r.pagina_fim - r.pagina_inicio + 1 for r in romaneios)
        return {
            "total_paginas": doc.page_count,
            "romaneios_detectados": len(romaneios),
            "paginas_sem_classe": max(0, doc.page_count - total_class),
            "usou_ocr": any(r.ocr for r in romaneios),
            "qtd_informada": qtd_informada,
            "qtd_confere": (qtd_informada is None) or (qtd_informada == len(romaneios)),
            "romaneios": [r.to_dict() for r in romaneios],
        }
    finally:
        doc.close()
