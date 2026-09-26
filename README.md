# ROMANEIO HUB — Leitor Python (backend)

Serviço em **FastAPI + PyMuPDF** que recebe um PDF, separa os romaneios
em **pares frente+verso** (1 PDF por nota) e **lê os campos** (frota,
número do romaneio, viagem, motorista, data, placa, N# da carga).
Faz **OCR no servidor** para PDFs escaneados (imagem), com precisão e
velocidade muito maiores que no navegador.

## O que ele resolve

O app (romaneio-hub.html) lê PDFs **com texto** direto no navegador.
Para **PDFs escaneados** (imagem, sem texto), a leitura da frota só é
possível com OCR — e é isso que este backend faz.

---

## 1) Rodar localmente (teste rápido)

```bash
cd romaneio-hub-backend
pip install -r requirements.txt
# OCR opcional: instale o tesseract no sistema (Ubuntu/Debian):
#   sudo apt-get install -y tesseract-ocr tesseract-ocr-por
export OCR_ENABLED=true
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Teste: abra http://localhost:8000/health → deve responder `{"ok": true, ...}`.

## 2) Rodar com Docker (recomendado — já traz o tesseract)

```bash
cd romaneio-hub-backend
docker build -t romaneio-leitor .
docker run -p 8000:8000 -e OCR_ENABLED=true romaneio-leitor
```

## 3) Deploy na nuvem (para o app usar de qualquer lugar)

**Render (grátis, mais fácil):**
1. Crie um repositório no GitHub e suba esta pasta.
2. Em https://render.com → **New +** → **Blueprint** → aponte para o repo.
   Ele lê o `render.yaml` e sobe com OCR ligado.
3. Ao terminar, copie a URL pública (ex.: `https://romaneio-hub-leitor.onrender.com`).

Alternativas equivalentes: **Railway**, **Fly.io**, ou uma **VPS** com Docker.

> Dica: no primeiro acesso o plano grátis do Render "acorda" o serviço
> (demora alguns segundos). Depois fica rápido.

---

## 4) Ligar o app ao backend

No arquivo **romaneio-hub.html**, no topo do `<script>`, troque:

```js
const PROC_API = "";
```

por (use a URL do seu deploy):

```js
const PROC_API = "https://romaneio-hub-leitor.onrender.com";
```

Pronto. A partir daí, ao anexar um PDF, o app envia para o Python, que
lê a frota (com OCR quando necessário) e devolve as notas já separadas.
Deixe `PROC_API = ""` para voltar a processar no navegador.

> **Segurança:** em produção, troque `ALLOWED_ORIGINS` de `*` para o
> domínio onde o app está hospedado.

---

## Endpoints

- `GET /health` — verificação.
- `POST /process` — `multipart/form-data` com:
  - `file`: o PDF
  - `data_informada` (opcional): `dd/mm/aaaa` (preenche a data das notas sem data)
  - `qtd_informada` (opcional): inteiro
  - Resposta: `{ total_paginas, romaneios_detectados, usou_ocr, romaneios: [ { ordem, frota, numero, viagem, carga, data, placa, motorista, pagina_inicio, pagina_fim, confianca, nome_arquivo, base64, linhas } ] }`

## Regras de leitura (calibráveis em `app/processing.py`)

- Separação **sempre em pares** (frente+verso): páginas ÷ 2 = nº de notas.
- `Frota: 2038` → frota; `ROMANEIO ... - 744109` → número; `Motorista: ...`;
  `Data: dd/mm/aaaa`; `N# da Carga: 1311`; placas (Cavalo/Carreta).
- Ajuste as expressões regulares no topo de `processing.py` conforme
  outros modelos de romaneio forem aparecendo.

## Segurança

Validação de tipo/assinatura `%PDF`, limite de tamanho e de páginas,
timeout de processamento, CORS por variável de ambiente, roda como
usuário não-root no container, não executa JavaScript embutido no PDF,
e nunca usa o nome do arquivo enviado como caminho no servidor.
