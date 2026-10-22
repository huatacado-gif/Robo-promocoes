import os
import json
import time
import logging
import threading
import requests

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import HTTPServer, BaseHTTPRequestHandler

from shopee_api import buscar_ofertas


# ==================================================
# CONFIGURAÇÕES
# ==================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

CANAIS = {
    "utilidades": os.getenv("CHANNEL_UTILIDADES", "").strip(),
    "ferramentas": os.getenv("CHANNEL_FERRAMENTAS", "").strip(),
    "eletronicos": os.getenv("CHANNEL_ELETRONICOS", "").strip(),
}

MAX_POSTS_DIA = int(os.getenv("MAX_POSTS_DAY", "18"))
MAX_POR_CATEGORIA = 6

INTERVALO_BUSCA = 4 * 60 * 60  # 4 horas
FUSO = ZoneInfo("America/Sao_Paulo")

ARQUIVO_ESTADO = Path("estado_robo.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


# ==================================================
# CATEGORIAS E DESCONTOS MÍNIMOS
# ==================================================

CATEGORIAS = {
    "utilidades": {
        "nome": "Utilidades Domésticas",
        "desconto_minimo": 30,
        "termos": [
            "organizador de cozinha",
            "air fryer",
            "utensilios domesticos",
            "organizadores para casa",
        ],
    },
    "ferramentas": {
        "nome": "Ferramentas",
        "desconto_minimo": 25,
        "termos": [
            "parafusadeira",
            "furadeira",
            "jogo de ferramentas",
            "ferramentas eletricas",
        ],
    },
    "eletronicos": {
        "nome": "Eletrônicos",
        "desconto_minimo": 20,
        "termos": [
            "fone bluetooth",
            "smartwatch",
            "caixa de som bluetooth",
            "acessorios para celular",
        ],
    },
}


# ==================================================
# CONTROLE DE PUBLICAÇÕES E DUPLICATAS
# ==================================================

def carregar_estado():
    if not ARQUIVO_ESTADO.exists():
        return {"publicacoes": [], "vistos": []}

    try:
        with ARQUIVO_ESTADO.open("r", encoding="utf-8") as arquivo:
            estado = json.load(arquivo)

        estado.setdefault("publicacoes", [])
        estado.setdefault("vistos", [])
        return estado

    except (OSError, json.JSONDecodeError):
        logging.exception("Não foi possível ler o estado do robô.")
        return {"publicacoes": [], "vistos": []}


def salvar_estado(estado):
    temporario = ARQUIVO_ESTADO.with_suffix(".tmp")

    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(estado, arquivo, ensure_ascii=False, indent=2)

    temporario.replace(ARQUIVO_ESTADO)


def data_local():
    return datetime.now(FUSO).date().isoformat()


def publicacoes_hoje(estado):
    hoje = data_local()

    return [
        p for p in estado["publicacoes"]
        if p.get("data") == hoje
    ]


def chave_produto(produto):
    identificador = produto.get("id")

    if identificador:
        return str(identificador)

    return (
        produto.get("link_afiliado")
        or produto.get("link_produto")
        or produto.get("titulo", "").strip().lower()
    )


# ==================================================
# CONVERSÃO E VALIDAÇÃO DOS DADOS
# ==================================================

def numero(valor):
    """Converte valores numéricos retornados pela API."""

    if valor is None:
        return None

    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor).strip()
    texto = texto.replace("R$", "").replace(" ", "")

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
    desconto = desconto_percentual(
        produto.get("desconto_anunciado")
    )

    if not titulo or not link:
        return False

    if not link.startswith(("https://", "http://")):
        return False

    if preco is None or preco <= 0:
        return False

    if desconto is None:
        return False

    minimo = CATEGORIAS[categoria]["desconto_minimo"]

    if desconto < minimo:
        return False

    produto["_preco_numero"] = preco
    produto["_desconto_numero"] = desconto

    return True


# ==================================================
# BUSCA DE OFERTAS
# ==================================================

def buscar_candidatos():
    candidatos = []
    ids_desta_busca = set()

    for categoria, config in CATEGORIAS.items():
        for termo in config["termos"]:
            try:
                produtos = buscar_ofertas(termo)

                logging.info(
                    "Busca Shopee | %s | encontrados: %s",
                    termo,
                    len(produtos),
                )

                for produto in produtos:
                    if not validar_produto(produto, categoria):
                        continue

                    chave = chave_produto(produto)

                    if not chave or chave in ids_desta_busca:
                        continue

                    ids_desta_busca.add(chave)

                    produto["_categoria"] = categoria
                    produto["_chave"] = chave
                    candidatos.append(produto)

            except Exception:
                logging.exception(
                    "Erro ao buscar o termo: %s", termo
                )

    candidatos.sort(
        key=lambda p: p["_desconto_numero"],
        reverse=True,
    )

    logging.info(
        "Total de candidatos válidos: %s",
        len(candidatos),
    )

    return candidatos


# ==================================================
# ENVIO PARA O TELEGRAM
# ==================================================

def enviar_telegram(produto, categoria):
    canal = CANAIS.get(categoria)

    if not TELEGRAM_TOKEN or not canal:
        logging.error(
            "Token ou canal não configurado para %s.",
            categoria,
        )
        return False

    titulo = produto["titulo"]
    preco = produto["_preco_numero"]
    desconto = produto["_desconto_numero"]
    link = produto["link_afiliado"]
    foto = str(produto.get("foto") or "").strip()

    preco_formatado = (
        f"R$ {preco:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )

    mensagem = (
        f"🔥 OFERTA ENCONTRADA!\n\n"
        f"📦 {titulo}\n\n"
        f"💰 Preço anunciado: {preco_formatado}\n"
        f"🏷️ Desconto anunciado: {desconto:.0f}%\n\n"
        f"🛒 Confira aqui:\n{link}\n\n"
        f"ℹ️ Preço e disponibilidade podem mudar. "
        f"Consulte o valor atual no anúncio.\n\n"
    )

    base_url = (
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    )

    try:
        if foto.startswith(("https://", "http://")):
            resposta = requests.post(
                f"{base_url}/sendPhoto",
                data={
                    "chat_id": canal,
                    "photo": foto,
                    "caption": mensagem,
                },
                timeout=30,
            )
        else:
            resposta = requests.post(
                f"{base_url}/sendMessage",
                data={
                    "chat_id": canal,
                    "text": mensagem,
                    "disable_web_page_preview": False,
                },
                timeout=30,
            )

        resposta.raise_for_status()
        dados = resposta.json()

        if not dados.get("ok"):
            logging.error("Telegram recusou a publicação.")
            return False

        logging.info(
            "Oferta publicada no canal %s: %s",
            categoria,
            titulo,
        )
        return True

    except Exception:
        logging.exception("Falha ao publicar no Telegram.")
        return False


# ==================================================
# CICLO PRINCIPAL (CORRIGIDO CONTRA DUPLICATAS)
# ==================================================

def executar_ciclo():
    estado = carregar_estado()
    publicados = publicacoes_hoje(estado)

    restantes = MAX_POSTS_DIA - len(publicados)

    if restantes <= 0:
        logging.info("Limite diário de publicações atingido.")
        return

    candidatos = buscar_candidatos()
    vistos = set(estado["vistos"])

    contagem_categoria = {}

    for publicacao in publicados:
        categoria = publicacao.get("categoria")
        contagem_categoria[categoria] = (
            contagem_categoria.get(categoria, 0) + 1
        )

    total_enviados = 0

    for produto in candidatos:
        if total_enviados >= restantes:
            break

        categoria = produto["_categoria"]
        chave = produto["_chave"]

        # Impede que o mesmo produto seja processado novamente
        if chave in vistos:
            continue

        # Marca como visto imediatamente na memória do ciclo
        vistos.add(chave)

        if contagem_categoria.get(categoria, 0) >= MAX_POR_CATEGORIA:
            continue

        if enviar_telegram(produto, categoria):
            agora = datetime.now(FUSO).isoformat()

            estado["publicacoes"].append({
                "data": data_local(),
                "data_hora": agora,
                "categoria": categoria,
                "id": chave,
                "titulo": produto["titulo"],
                "preco": produto["_preco_numero"],
                "desconto_anunciado": produto["_desconto_numero"],
            })

            estado["vistos"] = list(vistos)[-5000:]

            contagem_categoria[categoria] = (
                contagem_categoria.get(categoria, 0) + 1
            )

            total_enviados += 1
            salvar_estado(estado)

            time.sleep(3)

    logging.info(
        "Ciclo finalizado. Publicações realizadas: %s",
        total_enviados,
    )


# ==================================================
# MONITORAMENTO DO RENDER
# ==================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Robo de ofertas ativo")

    def log_message(self, format, *args):
        return


def run_web_server():
    porta = int(os.environ.get("PORT", "10000"))
    servidor = HTTPServer(("0.0.0.0", porta), HealthHandler)
    servidor.serve_forever()


def iniciar_robo():
    while True:
        try:
            executar_ciclo()
        except Exception:
            logging.exception("Erro inesperado no ciclo.")

        logging.info(
            "Próxima busca em aproximadamente %s horas.",
            INTERVALO_BUSCA / 3600,
        )
        time.sleep(INTERVALO_BUSCA)


if __name__ == "__main__":
    threading.Thread(
        target=run_web_server,
        daemon=True,
    ).start()

    iniciar_robo()
