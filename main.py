import os
import json
import time
import logging
import threading
import requests
import unicodedata
import re
import secrets
import hashlib
import html

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

from shopee_api import buscar_ofertas

# ==================================================
# CONFIGURAÇÕES DE AMBIENTE
# ==================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()
SUPABASE_TIMEOUT = 20

CANAIS = {
    "utilidades": os.getenv("CHANNEL_UTILIDADES", "").strip(),
    "ferramentas": os.getenv("CHANNEL_FERRAMENTAS", "").strip(),
    "eletronicos": os.getenv("CHANNEL_ELETRONICOS", "").strip(),
}

MAX_POSTS_DIA = int(os.getenv("MAX_POSTS_DAY", "18"))
MAX_POR_CATEGORIA = 2

HORARIOS_PUBLICACAO = [(6, 30), (11, 0), (19, 0)]
INTERVALO_VERIFICACAO = 20
FUSO = ZoneInfo("America/Sao_Paulo")
ARQUIVO_ESTADO = Path("estado_robo.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

# ==================================================
# CATEGORIAS E TERMOS DE BUSCA
# ==================================================

CATEGORIAS = {
    "utilidades": {
        "nome": "Casa, cozinha e utilidades",
        "desconto_minimo": 25,
        "termos": [
            "air fryer", "jogo de panelas", "liquidificador", "organizador de cozinha",
            "organizador para casa", "aspirador de po", "ventilador", "luminaria led",
            "garrafa termica", "utensilios de cozinha", "achadinhos para casa"
        ],
    },
    "ferramentas": {
        "nome": "Ferramentas",
        "desconto_minimo": 25,
        "termos": [
            "parafusadeira", "furadeira", "kit ferramentas", "jogo de chaves",
            "maleta de ferramentas", "chave de impacto", "esmerilhadeira",
            "maquina de solda", "multimetro", "trena eletrica", "ferramentas eletricas"
        ],
    },
    "eletronicos": {
        "nome": "Eletrônicos",
        "desconto_minimo": 25,
        "termos": [
            "fone bluetooth", "smartwatch", "caixa de som bluetooth", "carregador turbo",
            "carregador sem fio", "power bank", "teclado e mouse", "camera wifi",
            "suporte celular", "lampada inteligente"
        ],
    },
}

# ==================================================
# ESTADO LOCAL E SUPABASE
# ==================================================

def carregar_estado():
    if not ARQUIVO_ESTADO.exists():
        return {"publicacoes": [], "vistos": [], "execucoes_agendadas": {}}
    try:
        with ARQUIVO_ESTADO.open("r", encoding="utf-8") as arquivo:
            estado = json.load(arquivo)
        estado.setdefault("publicacoes", [])
        estado.setdefault("vistos", [])
        estado.setdefault("execucoes_agendadas", {})
        return estado
    except (OSError, json.JSONDecodeError):
        logging.exception("Não foi possível ler o estado local do robô.")
        return {"publicacoes": [], "vistos": [], "execucoes_agendadas": {}}

def salvar_estado(estado):
    temporario = ARQUIVO_ESTADO.with_suffix(".tmp")
    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(estado, arquivo, ensure_ascii=False, indent=2)
    temporario.replace(ARQUIVO_ESTADO)

def data_local():
    return datetime.now(FUSO).date().isoformat()

def publicacoes_hoje(estado):
    hoje = data_local()
    return [p for p in estado["publicacoes"] if p.get("data") == hoje]

def chave_produto(produto):
    identificador = produto.get("id")
    if identificador:
        return str(identificador)
    return (
        produto.get("link_afiliado")
        or produto.get("link_produto")
        or produto.get("titulo", "").strip().lower()
    )

def normalizar_titulo(titulo):
    texto = unicodedata.normalize("NFKD", str(titulo or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return " ".join(texto.split())

def supabase_headers(prefer=None):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Supabase não configurado nas variáveis de ambiente.")
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers

def carregar_historico_supabase():
    url = f"{SUPABASE_URL}/rest/v1/produtos_publicados"
    ids, titulos = set(), set()
    inicio = 0
    while True:
        headers = supabase_headers()
        headers["Range-Unit"] = "items"
        headers["Range"] = f"{inicio}-{inicio + 999}"
        resposta = requests.get(
            url,
            params={"select": "id_shopee,titulo_normalizado"},
            headers=headers,
            timeout=SUPABASE_TIMEOUT,
        )
        resposta.raise_for_status()
        linhas = resposta.json()
        if not isinstance(linhas, list):
            raise RuntimeError("Resposta inesperada do Supabase.")
        for linha in linhas:
            if linha.get("id_shopee"):
                ids.add(str(linha["id_shopee"]))
            if linha.get("titulo_normalizado"):
                titulos.add(linha["titulo_normalizado"])
        if len(linhas) < 1000:
            break
        inicio += len(linhas)
    return ids, titulos

def reservar_no_supabase(chave, titulo, origem="shopee"):
    url = f"{SUPABASE_URL}/rest/v1/produtos_publicados"
    registro = {
        "id_shopee": str(chave),
        "titulo": str(titulo or ""),
        "titulo_normalizado": normalizar_titulo(titulo),
        "origem": origem,
    }
    resposta = requests.post(
        url,
        json=registro,
        headers=supabase_headers("resolution=ignore-duplicates,return=representation"),
        timeout=SUPABASE_TIMEOUT,
    )
    resposta.raise_for_status()
    resultado = resposta.json()
    return isinstance(resultado, list) and len(resultado) > 0

# ==================================================
# VALIDAÇÃO E FILTROS DE PRODUTOS
# ==================================================

def numero(valor):
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace("R$", "").replace(" ", "")
    if not texto:
        return None
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None

def desconto_percentual(valor):
    desconto = numero(valor)
    if desconto is None:
        return None
    if 0 < desconto < 1:
        desconto *= 100
    if desconto < 0 or desconto > 100:
        return None
    return desconto

def validar_produto(produto, categoria):
    titulo = str(produto.get("titulo") or "").strip()
    link = str(produto.get("link_afiliado") or "").strip()
    preco = numero(produto.get("preco_min"))
    desconto = desconto_percentual(produto.get("desconto_anunciado"))

    if not titulo or not link or not link.startswith(("https://", "http://")):
        return False
    if preco is None or preco <= 0 or desconto is None:
        return False

    avaliacao = numero(produto.get("avaliacao"))
    total_avaliacoes = None
    for campo in ("quantidade_avaliacoes", "total_avaliacoes", "numero_avaliacoes", "avaliacoes", "review_count"):
        valor = produto.get(campo)
        if valor is not None:
            total_avaliacoes = numero(valor)
            if total_avaliacoes is not None:
                break

    minimo = CATEGORIAS[categoria]["desconto_minimo"]
    if avaliacao is not None and avaliacao >= 4.5 and total_avaliacoes is not None and total_avaliacoes >= 20:
        minimo = 15

    if avaliacao is not None and avaliacao < 4.0 and total_avaliacoes is not None and total_avaliacoes >= 20:
        return False

    if desconto < minimo:
        return False

    produto["_preco_numero"] = preco
    produto["_desconto_numero"] = desconto
    produto["_avaliacao_numero"] = avaliacao
    produto["_total_avaliacoes"] = total_avaliacoes
    return True

def buscar_candidatos():
    candidatos = []
    ids_desta_busca = set()

    termos_bloqueados = [
        "motor", "motores", "motor de partida", "motor elétrico", "motor eletrico",
        "motor de moto", "peça de motor", "peca de motor", "saco para aspirador",
        "sacos para aspirador", "refil", "peca de reposicao", "peça de reposição",
        "reposicao", "borracha de vedacao", "parafuso avulso", "lixa", "lixas",
        "lixa de unha", "unha postica", "brinquedo de ferramentas", "kit ferramentas infantil",
        "chaveiro ferramenta", "acessorio decorativo"
    ]

    termos_infantis = ["infantil", "brinquedo", "brinquedos", "didatico", "para crianca", "para criancas"]

    def normalizar(texto):
        texto = str(texto or "").lower()
        texto = unicodedata.normalize("NFKD", texto)
        return "".join(c for c in texto if not unicodedata.combining(c))

    for categoria, config in CATEGORIAS.items():
        for termo in config["termos"]:
            try:
                produtos = buscar_ofertas(termo)
                for produto in produtos:
                    if not validar_produto(produto, categoria):
                        continue
                    titulo = normalizar(produto.get("titulo", ""))

                    if categoria in ("ferramentas", "eletronicos") and any(item in titulo for item in termos_infantis):
                        continue

                    if any(item in titulo for item in termos_bloqueados):
                        continue

                    chave = chave_produto(produto)
                    if not chave or chave in ids_desta_busca:
                        continue

                    ids_desta_busca.add(chave)
                    produto["_categoria"] = categoria
                    produto["_chave"] = chave
                    candidatos.append(produto)
            except Exception:
                logging.exception("Erro ao buscar o termo: %s", termo)

    def numero_seguro(val):
        try:
            return float(val or 0)
        except (TypeError, ValueError):
            return 0.0

    candidatos.sort(
        key=lambda p: (
            numero_seguro(p.get("vendas")),
            numero_seguro(p.get("avaliacao")),
            numero_seguro(p.get("_desconto_numero")),
        ),
        reverse=True,
    )
    return candidatos

# ==================================================
# ENVIO TELEGRAM
# ==================================================

def enviar_telegram(produto, categoria):
    canal = CANAIS.get(categoria)
    if not TELEGRAM_TOKEN or not canal:
        logging.error("Token ou canal não configurado para %s.", categoria)
        return False

    titulo = str(produto.get("titulo") or "Oferta especial").strip()
    preco = produto["_preco_numero"]
    desconto = produto["_desconto_numero"]
    link = str(produto.get("link_afiliado") or "").strip()
    foto = str(produto.get("foto") or "").strip()

    def formatar_preco(valor):
        return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    if len(titulo) > 100:
        titulo = titulo[:100].rsplit(" ", 1)[0].rstrip(" ,.-") + "..."

    preco_formatado = formatar_preco(preco)
    preco_original = preco / (1 - desconto / 100) if 0 < desconto < 100 and preco > 0 else None

    if preco_original and preco_original > preco:
        linha_preco = f"💸 <s>De: {formatar_preco(preco_original)}</s>\n🔥 <b>Por: {preco_formatado}</b>\n"
    else:
        linha_preco = f"🔥 <b>Preço: {preco_formatado}</b>\n"

    mensagem = (
        f"🔥 <b>OFERTA ENCONTRADA!</b>\n\n"
        f"📦 <b>{html.escape(titulo)}</b>\n\n"
        f"{linha_preco}"
        f"🏷️ <b>{desconto:.0f}% OFF</b>\n\n"
        f"🛒 <a href=\"{html.escape(link)}\">COMPRAR AGORA</a>\n\n"
        f"<i>Confira o valor atualizado no site da loja.</i>"
    )

    base_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    try:
        if foto.startswith(("https://", "http://")):
            resposta = requests.post(
                f"{base_url}/sendPhoto",
                data={"chat_id": canal, "photo": foto, "caption": mensagem, "parse_mode": "HTML"},
                timeout=30,
            )
        else:
            resposta = requests.post(
                f"{base_url}/sendMessage",
                data={"chat_id": canal, "text": mensagem, "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=30,
            )
        resposta.raise_for_status()
        return resposta.json().get("ok", False)
    except Exception:
        logging.exception("Falha ao publicar no Telegram.")
        return False

# ==================================================
# CICLO PRINCIPAL DE EXECUÇÃO
# ==================================================

def executar_ciclo(horario_slot):
    estado = carregar_estado()
    chave_slot = f"{data_local()}|{horario_slot}"

    try:
        ids_publicados, titulos_publicados = carregar_historico_supabase()
    except Exception:
        logging.exception("Ciclo interrompido: Supabase indisponível.")
        return

    publicados = publicacoes_hoje(estado)
    restantes = MAX_POSTS_DIA - len(publicados)
    if restantes <= 0:
        logging.info("Limite diário de publicações atingido.")
        return

    candidatos = buscar_candidatos()
    vistos = set(estado.get("vistos", []))

    contadores_execucao = {"utilidades": 0, "ferramentas": 0, "eletronicos": 0}
    total_enviados = 0

    for produto in candidatos:
        if total_enviados >= restantes:
            break

        categoria = produto["_categoria"]
        if contadores_execucao[categoria] >= MAX_POR_CATEGORIA:
            continue

        chave = str(produto["_chave"])
        titulo = produto.get("titulo", "")
        titulo_norm = normalizar_titulo(titulo)

        if chave in vistos or chave in ids_publicados or (titulo_norm and titulo_norm in titulos_publicados):
            continue

        try:
            if not reservar_no_supabase(chave, titulo, origem="shopee"):
                continue
        except Exception:
            logging.exception("Erro ao reservar oferta no Supabase.")
            return

        if enviar_telegram(produto, categoria):
            estado["publicacoes"].append({
                "data": data_local(),
                "data_hora": datetime.now(FUSO).isoformat(),
                "horario_slot": horario_slot,
                "categoria": categoria,
                "id": chave,
                "titulo": titulo,
                "preco": produto["_preco_numero"],
                "origem": "shopee",
            })
            vistos.add(chave)
            estado["vistos"] = list(vistos)[-5000:]
            contadores_execucao[categoria] += 1
            total_enviados += 1
            salvar_estado(estado)
            ids_publicados.add(chave)
            if titulo_norm:
                titulos_publicados.add(titulo_norm)
            time.sleep(3)

    estado.setdefault("execucoes_agendadas", {})[chave_slot] = "concluido"
    salvar_estado(estado)
    logging.info("Lote %s concluído. Ofertas enviadas: %s", horario_slot, total_enviados)

# ==================================================
# COMANDOS E WEBHOOK TELEGRAM
# ==================================================

SESSOES_ML = {}
EXECUCAO_MANUAL_LOCK = threading.Lock()

def responder_usuario(chat_id, texto):
    if not TELEGRAM_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": texto, "disable_web_page_preview": True},
            timeout=20,
        )
    except Exception:
        logging.exception("Erro ao responder ao usuário no Telegram.")

def processar_mensagem_telegram(mensagem):
    if not isinstance(mensagem, dict):
        return
    chat = mensagem.get("chat") or {}
    remetente = mensagem.get("from") or {}
    chat_id = chat.get("id")
    user_id = str(remetente.get("id", ""))
    texto = str(mensagem.get("text") or "").strip()

    if not chat_id or not user_id or not texto:
        return

    comando = texto.split()[0].split("@")[0].lower()

    if chat.get("type") == "private" and comando == "/id":
        responder_usuario(chat_id, f"Seu ID do Telegram é: {user_id}")
        return

    if chat.get("type") != "private" or (TELEGRAM_ADMIN_ID and user_id != TELEGRAM_ADMIN_ID):
        return

    if comando == "/start":
        menu = (
            "🤖 <b>Bem-vindo ao Painel do Robô de Promoções!</b>\n\n"
            "Comandos disponíveis:\n"
            "🔹 /sh — Iniciar busca de ofertas na Shopee\n"
            "🔹 /ml — Cadastrar oferta manual (Mercado Livre, Magalu, Amazon, AliExpress, TikTok)\n"
            "🔹 /ajuda — Exibir ajuda\n"
            "🔹 /cancelar — Cancelar cadastro manual atual"
        )
        responder_usuario(chat_id, menu)
        return

    if comando == "/ajuda":
        responder_usuario(chat_id, "Use /sh para disparar a Shopee ou /ml para cadastrar links de afiliado manualmente.")
        return

    if comando == "/cancelar":
        SESSOES_ML.pop(user_id, None)
        responder_usuario(chat_id, "Cadastro cancelado com sucesso.")
        return

    if comando == "/sh":
        if EXECUCAO_MANUAL_LOCK.locked():
            responder_usuario(chat_id, "A busca da Shopee já está em andamento.")
            return
        responder_usuario(chat_id, "🚀 Busca manual iniciada! Buscando produtos nas 3 categorias...")
        
        def rodar():
            if EXECUCAO_MANUAL_LOCK.acquire(blocking=False):
                try:
                    executar_ciclo("manual")
                    responder_usuario(chat_id, "✅ Busca manual da Shopee finalizada com sucesso!")
                except Exception:
                    responder_usuario(chat_id, "⚠️ Ocorreu um erro durante a busca. Verifique os logs.")
                finally:
                    EXECUCAO_MANUAL_LOCK.release()

        threading.Thread(target=rodar, daemon=True).start()
        return

    if comando == "/ml":
        SESSOES_ML[user_id] = {"chat_id": chat_id, "etapa": "link"}
        responder_usuario(chat_id, "1/5 — Envie o link de afiliado (Mercado Livre, Magalu, Amazon, AliExpress ou TikTok).")
        return

    sessao = SESSOES_ML.get(user_id)
    if not sessao:
        return

    etapa = sessao["etapa"]
    if etapa == "link":
        host = (urlparse(texto).hostname or "").lower()
        plataforma = "desconhecida"
        if "mercadolivre" in host or "meli.la" in host:
            plataforma = "Mercado Livre"
        elif "magazineluiza" in host or "magalu" in host:
            plataforma = "Magalu"
        elif "amazon" in host or "amzn" in host:
            plataforma = "Amazon"
        elif "aliexpress" in host or "ali.ski" in host:
            plataforma = "AliExpress"
        elif "tiktok" in host:
            plataforma = "TikTok Shop"

        sessao["link"] = texto
        sessao["plataforma"] = plataforma
        sessao["etapa"] = "titulo"
        responder_usuario(chat_id, f"Plataforma identificada: {plataforma}\n2/5 — Envie o nome do produto.")
    elif etapa == "titulo":
        sessao["titulo"] = texto
        sessao["etapa"] = "preco_atual"
        responder_usuario(chat_id, "3/5 — Qual é o preço atual? (Ex: 149,90)")
    elif etapa == "preco_atual":
        val = numero(texto)
        if not val or val <= 0:
            responder_usuario(chat_id, "Preço inválido. Digite um valor numérico.")
            return
        sessao["preco_atual"] = val
        sessao["etapa"] = "preco_anterior"
        responder_usuario(chat_id, "4/5 — Qual o preço anterior? (Digite 0 se não houver)")
    elif etapa == "preco_anterior":
        val = numero(texto)
        sessao["preco_anterior"] = val if val and val > 0 else 0
        sessao["etapa"] = "categoria"
        responder_usuario(chat_id, "5/5 — Escolha a categoria: utilidades, ferramentas ou eletronicos")
    elif etapa == "categoria":
        cat = texto.lower().strip()
        if cat not in CANAIS:
            responder_usuario(chat_id, "Categoria inválida. Escolha: utilidades, ferramentas ou eletronicos.")
            return

        chave = "manual:" + hashlib.sha256(sessao["link"].encode()).hexdigest()[:24]
        try:
            if not reservar_no_supabase(chave, sessao["titulo"], origem=sessao["plataforma"].lower().replace(" ", "")):
                responder_usuario(chat_id, "Esta oferta já foi cadastrada anteriormente.")
                SESSOES_ML.pop(user_id, None)
                return
        except Exception:
            responder_usuario(chat_id, "Erro ao registrar no Supabase.")
            SESSOES_ML.pop(user_id, None)
            return

        p_atual = sessao["preco_atual"]
        p_ant = sessao["preco_anterior"]
        desc = round((1 - p_atual / p_ant) * 100) if p_ant > p_atual else 0

        msg = (
            f"🔥 <b>OFERTA ({sessao['plataforma'].upper()})!</b>\n\n"
            f"📦 <b>{html.escape(sessao['titulo'])}</b>\n\n"
            f"🔥 <b>Por: R$ {p_atual:,.2f}</b>\n"
            + (f"🏷️ <b>{desc}% OFF</b>\n" if desc > 0 else "")
            + f"\n🛒 <a href=\"{html.escape(sessao['link'])}\">COMPRAR AGORA</a>"
        )

        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": CANAIS[cat], "text": msg, "parse_mode": "HTML"},
                timeout=20,
            )
            responder_usuario(chat_id, "✅ Oferta cadastrada e publicada com sucesso!")
        except Exception:
            responder_usuario(chat_id, "Falha ao enviar mensagem para o canal Telegram.")

        SESSOES_ML.pop(user_id, None)

# ==================================================
# SERVIDOR WEB E WEBHOOK
# ==================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Robo Ativo")

    def do_POST(self):
        if urlparse(self.path).path != "/telegram/webhook":
            self.send_response(404)
            self.end_headers()
            return

        segredo = TELEGRAM_WEBHOOK_SECRET or hashlib.sha256((TELEGRAM_TOKEN + ":telegram-webhook").encode()).hexdigest()
        recebido = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not secrets.compare_digest(recebido, segredo):
            self.send_response(403)
            self.end_headers()
            return

        tamanho = int(self.headers.get("Content-Length", "0"))
        corpo = self.rfile.read(tamanho)
        update = json.loads(corpo.decode("utf-8"))
        processar_mensagem_telegram(update.get("message"))

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        return

def configurar_webhook():
    if not TELEGRAM_TOKEN:
        return
    host = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if not host:
        return
    segredo = TELEGRAM_WEBHOOK_SECRET or hashlib.sha256((TELEGRAM_TOKEN + ":telegram-webhook").encode()).hexdigest()
    url = f"{host}/telegram/webhook"
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            json={"url": url, "secret_token": segredo, "allowed_updates": ["message"]},
            timeout=20,
        )
    except Exception:
        logging.exception("Erro ao configurar Webhook.")

def iniciar_robo():
    while True:
        try:
            agora = datetime.now(FUSO)
            estado = carregar_estado()
            execucoes = estado.setdefault("execucoes_agendadas", {})
            hoje = agora.date().isoformat()

            for hora, minuto in HORARIOS_PUBLICACAO:
                horario_slot = f"{hora:02d}:{minuto:02d}"
                chave_slot = f"{hoje}|{horario_slot}"
                instante_slot = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)

                if agora >= instante_slot and chave_slot not in execucoes:
                    executar_ciclo(horario_slot)

            time.sleep(INTERVALO_VERIFICACAO)
        except Exception:
            logging.exception("Erro no agendador.")
            time.sleep(INTERVALO_VERIFICACAO)

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "10000"))
    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", porta), HealthHandler).serve_forever(), daemon=True).start()
    time.sleep(2)
    configurar_webhook()
    iniciar_robo()
