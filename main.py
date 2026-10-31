
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
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

from shopee_api import buscar_ofertas


# ==================================================
# CONFIGURAÇÕES
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

MAX_OFERTAS_SH = 2

FUSO = ZoneInfo("America/Sao_Paulo")
ARQUIVO_ESTADO = Path("estado_robo.json")

EXECUCAO_LOCK = threading.Lock()
SESSOES_ML = {}
CATEGORIAS_VALIDAS = ("utilidades", "ferramentas", "eletronicos")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


# ==================================================
# MARKETPLACES
# ==================================================

MARKETPLACES = {
    "1": {
        "nome": "Mercado Livre",
        "codigo": "mercadolivre",
        "dominios": (
            "mercadolivre.com.br",
            "mercadolivre.com",
            "mercadolibre.com",
            "meli.la",
        ),
    },
    "2": {
        "nome": "Magalu",
        "codigo": "magalu",
        "dominios": (
            "magazineluiza.com.br",
            "magalu.com.br",
        ),
    },
    "3": {
        "nome": "Amazon",
        "codigo": "amazon",
        "dominios": (
            "amazon.com.br",
            "amazon.com",
            "amzn.to",
        ),
    },
    "4": {
        "nome": "TikTok Shop",
        "codigo": "tiktok",
        "dominios": (
            "tiktok.com",
            "tiktokshop.com",
        ),
    },
    "5": {
        "nome": "AliExpress",
        "codigo": "aliexpress",
        "dominios": (
            "aliexpress.com",
            "a.aliexpress.com",
        ),
    },
}


# ==================================================
# CATEGORIAS SHOPEE
# ==================================================

CATEGORIAS = {
    "utilidades": {
        "nome": "Casa, cozinha e achadinhos",
        "desconto_minimo": 30,
        "termos": [
            "air fryer",
            "jogo de panelas",
            "liquidificador",
            "organizador de cozinha",
            "organizador para casa",
            "aspirador de po",
            "ventilador",
            "luminaria led",
            "garrafa termica",
            "utensilios de cozinha",
            "achadinhos para casa",
            "utensilios domesticos",
            "acessorios para cozinha",
            "organizadores domesticos",
        ],
    },

    "ferramentas": {
        "nome": "Ferramentas",
        "desconto_minimo": 30,
        "termos": [
            "parafusadeira",
            "furadeira",
            "kit ferramentas",
            "jogo de chaves",
            "maleta de ferramentas",
            "chave de impacto",
            "ferramentas eletricas",
            "esmerilhadeira",
            "maquina de solda",
            "multimetro digital",
            "trena digital",
            "kit brocas",
            "kit bits para parafusadeira",
            "acessorios para ferramentas",
            "jogo de soquetes",
        ],
    },

    "eletronicos": {
        "nome": "Eletrônicos e acessórios para celular",
        "desconto_minimo": 30,
        "termos": [
            "fone bluetooth",
            "smartwatch",
            "caixa de som bluetooth",
            "carregador turbo",
            "carregador sem fio",
            "cabo usb c",
            "suporte celular",
            "power bank",
            "teclado sem fio",
            "mouse sem fio",
            "lampada inteligente",
            "camera wifi",
            "acessorios gamer",
            "camera de seguranca wifi",
            "fone sem fio",
            "hub usb",
            "adaptador bluetooth",
            "webcam",
            "roteador wifi",
        ],
    },
}


# ==================================================
# FUNÇÕES GERAIS
# ==================================================

def agora_local():
    return datetime.now(FUSO)


def data_local():
    return agora_local().date().isoformat()


def normalizar_titulo(titulo):
    texto = unicodedata.normalize("NFKD", str(titulo or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return " ".join(texto.split())


def numero(valor):
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


def formatar_reais(valor):
    return (
        f"R$ {valor:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


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
# ESTADO LOCAL
# ==================================================

def carregar_estado():
    padrao = {
        "publicacoes": [],
        "vistos": [],
    }

    if not ARQUIVO_ESTADO.exists():
        return padrao

    try:
        with ARQUIVO_ESTADO.open("r", encoding="utf-8") as arquivo:
            estado = json.load(arquivo)

        estado.setdefault("publicacoes", [])
        estado.setdefault("vistos", [])

        return estado

    except (OSError, json.JSONDecodeError):
        logging.exception("Erro ao carregar estado.")
        return padrao


def salvar_estado(estado):
    temporario = ARQUIVO_ESTADO.with_suffix(".tmp")

    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(estado, arquivo, ensure_ascii=False, indent=2)

    temporario.replace(ARQUIVO_ESTADO)


def registrar_publicacao(
    estado,
    chave,
    titulo,
    categoria,
    preco,
    origem,
    desconto=None,
):
    estado.setdefault("publicacoes", []).append({
        "data": data_local(),
        "data_hora": agora_local().isoformat(),
        "categoria": categoria,
        "id": str(chave),
        "titulo": str(titulo),
        "preco": preco,
        "desconto_anunciado": desconto,
        "origem": origem,
    })

    vistos = set(estado.get("vistos", []))
    vistos.add(str(chave))
    estado["vistos"] = list(vistos)[-5000:]

    salvar_estado(estado)


# ==================================================
# SUPABASE
# ==================================================

def supabase_headers(prefer=None):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Supabase não configurado.")

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

    ids = set()
    titulos = set()
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


def reservar_no_supabase(chave, titulo, origem):
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
        headers=supabase_headers(
            "resolution=ignore-duplicates,return=representation"
        ),
        timeout=SUPABASE_TIMEOUT,
    )

    resposta.raise_for_status()
    resultado = resposta.json()

    return isinstance(resultado, list) and len(resultado) > 0


def importar_historico_local(estado):
    url = f"{SUPABASE_URL}/rest/v1/produtos_publicados"

    for publicacao in estado.get("publicacoes", []):
        chave = publicacao.get("id")
        titulo = publicacao.get("titulo", "")

        if not chave:
            continue

        resposta = requests.post(
            url,
            json={
                "id_shopee": str(chave),
                "titulo": str(titulo or ""),
                "titulo_normalizado": normalizar_titulo(titulo),
                "origem": publicacao.get("origem", "shopee"),
            },
            headers=supabase_headers(
                "resolution=ignore-duplicates,return=minimal"
            ),
            timeout=SUPABASE_TIMEOUT,
        )

        resposta.raise_for_status()


# ==================================================
# VALIDAÇÃO SHOPEE
# ==================================================

def validar_produto(produto, categoria):
    titulo = str(produto.get("titulo") or "").strip()
    link = str(produto.get("link_afiliado") or "").strip()

    preco = numero(produto.get("preco_min"))
    desconto = desconto_percentual(
        produto.get("desconto_anunciado")
    )

    if not titulo or not link:
        return False

    if not link.startswith("https://"):
        return False

    if preco is None or preco <= 0:
        return False

    if desconto is None:
        return False

    avaliacao = numero(produto.get("avaliacao"))
    total_avaliacoes = None

    for campo in (
        "quantidade_avaliacoes",
        "total_avaliacoes",
        "numero_avaliacoes",
        "avaliacoes",
        "review_count",
    ):
        valor = produto.get(campo)

        if valor is not None:
            total_avaliacoes = numero(valor)
            if total_avaliacoes is not None:
                break

    minimo = CATEGORIAS[categoria]["desconto_minimo"]

    if (
        avaliacao is not None
        and avaliacao >= 4.5
        and total_avaliacoes is not None
        and total_avaliacoes >= 20
    ):
        minimo = 15

    if (
        avaliacao is not None
        and avaliacao < 4.0
        and total_avaliacoes is not None
        and total_avaliacoes >= 20
    ):
        return False

    if desconto < minimo:
        return False

    produto["_preco_numero"] = preco
    produto["_desconto_numero"] = desconto
    produto["_avaliacao_numero"] = avaliacao
    produto["_total_avaliacoes"] = total_avaliacoes

    return True


# ==================================================
# BUSCA SHOPEE
# ==================================================

def buscar_candidatos():
    candidatos = []
    ids_desta_busca = set()

    termos_bloqueados = [
        "saco para aspirador",
        "sacos para aspirador",
        "refil",
        "peca de reposicao",
        "reposicao",
        "borracha de vedacao",
        "parafuso avulso",
        "suporte de teto para tv",
        "suporte de parede para tv",
        "controle remoto compativel",
        "lixa de unha",
        "lima de unha",
        "polidor de unha",
        "unha postica",
        "brinquedo de ferramentas",
        "kit ferramentas infantil",
        "chaveiro ferramenta",
        "acessorio decorativo",
        "motor para aspirador",
        "puxador porta geladeira",
    ]

    termos_infantis = [
        "infantil",
        "brinquedo",
        "brinquedos",
        "didatico",
        "didatica",
        "para crianca",
        "para criancas",
        "de crianca",
        "de criancas",
    ]

    def normalizar(texto):
        texto = unicodedata.normalize("NFKD", str(texto or "").lower())
        return "".join(
            c for c in texto if not unicodedata.combining(c)
        )

    def numero_seguro(valor):
        try:
            return float(valor or 0)
        except (TypeError, ValueError):
            return 0.0

    # Cada termo é pesquisado independentemente.
    # Assim, nenhuma categoria fica limitada a uma busca geral.

    for categoria, config in CATEGORIAS.items():
        for termo in config["termos"]:
            try:
                produtos = buscar_ofertas(termo) or []

                logging.info(
                    "Shopee | categoria=%s | termo=%s | resultados=%s",
                    categoria,
                    termo,
                    len(produtos),
                )

                for produto in produtos:
                    if not isinstance(produto, dict):
                        continue

                    if not validar_produto(produto, categoria):
                        continue

                    titulo = normalizar(produto.get("titulo", ""))

                    if (
                        categoria in ("ferramentas", "eletronicos")
                        and any(item in titulo for item in termos_infantis)
                    ):
                        continue

                    if any(item in titulo for item in termos_bloqueados):
                        continue

                    if (
                        re.search(r"\bmotores?\b", titulo)
                        and "motorola" not in titulo
                    ):
                        continue

                    chave = str(chave_produto(produto))

                    if not chave or chave in ids_desta_busca:
                        continue

                    ids_desta_busca.add(chave)

                    produto["_categoria"] = categoria
                    produto["_chave"] = chave

                    candidatos.append(produto)

            except Exception:
                logging.exception("Erro na busca do termo: %s", termo)

    # Classificação geral: vendas, nota, desconto e quantidade
    # de avaliações. A seleção final será feita entre todas
    # as categorias, e não separadamente por categoria.

    candidatos.sort(
        key=lambda p: (
            numero_seguro(p.get("vendas")),
            numero_seguro(p.get("_avaliacao_numero")),
            numero_seguro(p.get("_total_avaliacoes")),
            numero_seguro(p.get("_desconto_numero")),
        ),
        reverse=True,
    )

    logging.info("Candidatos aprovados: %s", len(candidatos))

    return candidatos


# ==================================================
# ENVIO SHOPEE
# ==================================================

def enviar_telegram(produto, categoria):
    canal = CANAIS.get(categoria)

    if not TELEGRAM_TOKEN or not canal:
        logging.error("Token ou canal não configurado: %s", categoria)
        return False

    titulo_original = str(produto.get("titulo") or "Oferta especial").strip()
    titulo = html.escape(titulo_original)

    preco = produto["_preco_numero"]
    desconto = produto["_desconto_numero"]

    link_original = str(produto.get("link_afiliado") or "").strip()

    if not link_original.startswith("https://"):
        return False

    link = html.escape(link_original, quote=True)
    foto = str(produto.get("foto") or "").strip()

    if len(titulo) > 400:
        titulo = titulo[:397] + "..."

    preco_original = None

    if 0 < desconto < 100:
        preco_original = preco / (1 - desconto / 100)

    if preco_original and preco_original > preco:
        linha_preco = (
            f"💸 <s>De: {formatar_reais(preco_original)}</s>\n"
            f"🔥 <b>Por: {formatar_reais(preco)}</b>\n"
        )
    else:
        linha_preco = f"🔥 <b>Preço: {formatar_reais(preco)}</b>\n"

    mensagem = (
        "🔥 <b>OFERTA ENCONTRADA!</b>\n\n"
        f"📦 <b>{titulo}</b>\n\n"
        f"{linha_preco}"
        f"🏷️ <b>{desconto:.0f}% OFF</b>\n\n"
        f"🛒 <a href=\"{link}\">COMPRAR AGORA</a>\n\n"
        "<i>Preço original estimado com base no desconto anunciado. "
        "Confira o valor atual no anúncio.</i>"
    )

    base_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

    try:
        if foto.startswith("https://"):
            resposta = requests.post(
                f"{base_url}/sendPhoto",
                data={
                    "chat_id": canal,
                    "photo": foto,
                    "caption": mensagem,
                    "parse_mode": "HTML",
                },
                timeout=30,
            )
        else:
            resposta = requests.post(
                f"{base_url}/sendMessage",
                data={
                    "chat_id": canal,
                    "text": mensagem,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=30,
            )

        resposta.raise_for_status()
        dados = resposta.json()

        if not dados.get("ok"):
            logging.error("Telegram: %s", dados.get("description"))
            return False

        logging.info("Shopee publicada em %s: %s", categoria, titulo_original)
        return True

    except Exception:
        logging.exception("Falha ao publicar oferta Shopee.")
        return False


# ==================================================
# EXECUÇÃO EXCLUSIVA DO /sh
# ==================================================

def executar_sh():
    if not EXECUCAO_LOCK.acquire(blocking=False):
        logging.warning("Já existe uma execução em andamento.")
        return False, "Já existe uma execução em andamento."

    try:
        estado = carregar_estado()

        # Importa o histórico local e consulta o histórico permanente.
        importar_historico_local(estado)
        ids_publicados, titulos_publicados = carregar_historico_supabase()

        vistos = set(estado.get("vistos", []))

        # Pesquisa todas as categorias antes de selecionar.
        candidatos = buscar_candidatos()

        total_enviados = 0

        for produto in candidatos:
            if total_enviados >= MAX_OFERTAS_SH:
                break

            categoria = produto["_categoria"]
            chave = str(produto["_chave"])
            titulo = produto.get("titulo", "")
            titulo_normalizado = normalizar_titulo(titulo)

            if chave in vistos or chave in ids_publicados:
                continue

            if titulo_normalizado in titulos_publicados:
                continue

            # Reserva no banco antes de publicar para reduzir
            # o risco de duplicidade em reinicializações.
            reservada = reservar_no_supabase(
                chave,
                titulo,
                origem="shopee",
            )

            if not reservada:
                continue

            if enviar_telegram(produto, categoria):
                registrar_publicacao(
                    estado=estado,
                    chave=chave,
                    titulo=titulo,
                    categoria=categoria,
                    preco=produto["_preco_numero"],
                    origem="shopee",
                    desconto=produto["_desconto_numero"],
                )

                vistos.add(chave)
                ids_publicados.add(chave)

                if titulo_normalizado:
                    titulos_publicados.add(titulo_normalizado)

                total_enviados += 1
                time.sleep(2)

            else:
                logging.warning(
                    "Falha no envio. Reserva mantida: %s",
                    titulo,
                )

        mensagem = (
            f"Busca concluída. Ofertas publicadas: "
            f"{total_enviados}/{MAX_OFERTAS_SH}."
        )

        logging.info(mensagem)
        return True, mensagem

    except Exception as erro:
        logging.exception("Erro na execução Shopee.")
        return False, f"Erro na execução: {erro}"

    finally:
        EXECUCAO_LOCK.release()


# ==================================================
# TELEGRAM - RESPOSTAS
# ==================================================

def responder_usuario(chat_id, texto):
    if not TELEGRAM_TOKEN:
        return

    try:
        resposta = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": texto,
                "disable_web_page_preview": True,
            },
            timeout=20,
        )

        resposta.raise_for_status()

    except Exception:
        logging.exception("Erro ao responder usuário.")


# ==================================================
# CADASTRO MANUAL MULTIMARKETPLACE
# ==================================================

def identificar_marketplace(link, marketplace_id):
    try:
        parsed = urlparse(link)
        host = (parsed.hostname or "").lower()

        if parsed.scheme != "https":
            return False

        config = MARKETPLACES.get(str(marketplace_id))

        if not config:
            return False

        return any(
            host == dominio or host.endswith("." + dominio)
            for dominio in config["dominios"]
        )

    except Exception:
        return False


def enviar_oferta_manual(produto, categoria):
    canal = CANAIS.get(categoria)

    if not TELEGRAM_TOKEN or not canal:
        logging.error("Canal não configurado: %s", categoria)
        return False

    titulo = html.escape(produto["titulo"])
    link = html.escape(produto["link_afiliado"], quote=True)

    preco_atual = produto["preco_atual"]
    preco_anterior = produto["preco_anterior"]
    marketplace = produto["marketplace_nome"]

    linhas_preco = []

    if preco_anterior > preco_atual:
        linhas_preco.append(
            f"💸 <s>De: {formatar_reais(preco_anterior)}</s>"
        )

    linhas_preco.append(
        f"🔥 <b>Por: {formatar_reais(preco_atual)}</b>"
    )

    desconto = 0

    if preco_anterior > preco_atual:
        desconto = round(
            (1 - preco_atual / preco_anterior) * 100
        )

    mensagem = (
        f"🔥 <b>OFERTA {html.escape(marketplace.upper())}!</b>\n\n"
        f"📦 <b>{titulo}</b>\n\n"
        + "\n".join(linhas_preco)
        + (
            f"\n🏷️ <b>{desconto}% OFF</b>"
            if desconto > 0 else ""
        )
        + f"\n\n🛒 <a href=\"{link}\">COMPRAR AGORA</a>"
        + "\n\n<i>Preço e disponibilidade podem mudar. "
        "Confira o anúncio.</i>"
    )

    try:
        resposta = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id": canal,
                "text": mensagem,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=30,
        )

        resposta.raise_for_status()
        dados = resposta.json()

        if not dados.get("ok"):
            logging.error("Telegram: %s", dados.get("description"))
            return False

        logging.info(
            "Oferta manual publicada: %s | %s",
            marketplace,
            produto["titulo"],
        )

        return True

    except Exception:
        logging.exception("Erro ao publicar oferta manual.")
        return False


def iniciar_cadastro_ml(chat_id, user_id):
    SESSOES_ML[str(user_id)] = {
        "chat_id": chat_id,
        "etapa": "marketplace",
    }

    responder_usuario(
        chat_id,
        "🛒 CADASTRO MANUAL DE OFERTA\n\n"
        "Escolha o marketplace:\n\n"
        "1 — Mercado Livre\n"
        "2 — Magalu\n"
        "3 — Amazon\n"
        "4 — TikTok Shop\n"
        "5 — AliExpress\n\n"
        "Digite o número da opção.\n"
        "Para sair, envie /cancelar."
    )


def finalizar_cadastro_manual(chat_id, user_id, sessao, categoria):
    if not EXECUCAO_LOCK.acquire(blocking=False):
        responder_usuario(
            chat_id,
            "Já existe uma publicação em andamento. Aguarde."
        )
        return

    try:
        estado = carregar_estado()

        produto = {
            "titulo": sessao["titulo"],
            "link_afiliado": sessao["link_afiliado"],
            "preco_atual": sessao["preco_atual"],
            "preco_anterior": sessao["preco_anterior"],
            "marketplace_nome": sessao["marketplace_nome"],
        }

        if not CANAIS.get(categoria):
            responder_usuario(
                chat_id,
                f"O canal {categoria} não está configurado no Render."
            )
            return

        codigo = sessao["marketplace_codigo"]

        chave = (
            codigo + ":"
            + hashlib.sha256(
                sessao["link_afiliado"].encode("utf-8")
            ).hexdigest()[:32]
        )

        try:
            ids, titulos = carregar_historico_supabase()

            titulo_normalizado = normalizar_titulo(produto["titulo"])

            if (
                chave in ids
                or titulo_normalizado in titulos
            ):
                responder_usuario(
                    chat_id,
                    "Essa oferta ou um título equivalente já consta "
                    "no histórico. Não publiquei para evitar duplicidade."
                )
                return

            reservada = reservar_no_supabase(
                chave,
                produto["titulo"],
                origem=codigo,
            )

            if not reservada:
                responder_usuario(
                    chat_id,
                    "O Supabase identificou uma possível duplicidade. "
                    "A oferta não foi publicada."
                )
                return

        except Exception:
            logging.exception("Erro ao consultar Supabase.")

            responder_usuario(
                chat_id,
                "Não consegui consultar ou registrar o histórico "
                "no Supabase. Não publiquei a oferta."
            )
            return

        if enviar_oferta_manual(produto, categoria):
            registrar_publicacao(
                estado=estado,
                chave=chave,
                titulo=produto["titulo"],
                categoria=categoria,
                preco=produto["preco_atual"],
                origem=codigo,
            )

            responder_usuario(
                chat_id,
                "✅ Oferta publicada e registrada no histórico!"
            )
        else:
            responder_usuario(
                chat_id,
                "⚠️ O envio falhou. A reserva ficou no histórico "
                "para evitar uma possível duplicação. Confira os logs."
            )

    finally:
        EXECUCAO_LOCK.release()


# ==================================================
# PROCESSAMENTO DO CADASTRO MANUAL
# ==================================================

def processar_etapa_manual(chat_id, user_id, texto, sessao):
    etapa = sessao["etapa"]

    if etapa == "marketplace":
        opcao = texto.strip()

        if opcao not in MARKETPLACES:
            responder_usuario(
                chat_id,
                "Opção inválida. Escolha de 1 a 5."
            )
            return

        config = MARKETPLACES[opcao]

        sessao["marketplace_codigo"] = config["codigo"]
        sessao["marketplace_nome"] = config["nome"]
        sessao["marketplace_id"] = opcao
        sessao["etapa"] = "link"

        responder_usuario(
            chat_id,
            f"Marketplace escolhido: {config['nome']}.\n\n"
            "1/5 — Envie agora o link de afiliado."
        )

    elif etapa == "link":
        if not identificar_marketplace(
            texto,
            sessao["marketplace_id"],
        ):
            responder_usuario(
                chat_id,
                "O domínio do link não corresponde ao marketplace "
                "escolhido ou o link não é HTTPS. Envie novamente."
            )
            return

        sessao["link_afiliado"] = texto.strip()
        sessao["etapa"] = "titulo"

        responder_usuario(
            chat_id,
            "2/5 — Envie o título do produto."
        )

    elif etapa == "titulo":
        if len(texto) < 3 or len(texto) > 180:
            responder_usuario(
                chat_id,
                "O título deve ter entre 3 e 180 caracteres."
            )
            return

        sessao["titulo"] = texto
        sessao["etapa"] = "preco_atual"

        responder_usuario(
            chat_id,
            "3/5 — Informe o preço atual.\nExemplo: 149,90"
        )

    elif etapa == "preco_atual":
        valor = numero(texto)

        if valor is None or valor <= 0:
            responder_usuario(
                chat_id,
                "Preço inválido. Envie um valor positivo, "
                "por exemplo: 149,90."
            )
            return

        sessao["preco_atual"] = valor
        sessao["etapa"] = "preco_anterior"

        responder_usuario(
            chat_id,
            "4/5 — Informe o preço anterior.\n"
            "Envie 0 se não houver preço anterior."
        )

    elif etapa == "preco_anterior":
        valor = numero(texto)

        if valor is None or valor < 0:
            responder_usuario(
                chat_id,
                "Preço inválido. Envie um valor positivo ou 0."
            )
            return

        if valor > 0 and valor <= sessao["preco_atual"]:
            responder_usuario(
                chat_id,
                "O preço anterior precisa ser maior que o atual. "
                "Envie novamente ou 0 se não houver."
            )
            return

        sessao["preco_anterior"] = valor
        sessao["etapa"] = "categoria"

        responder_usuario(
            chat_id,
            "5/5 — Escolha o canal de publicação:\n\n"
            "utilidades\n"
            "ferramentas\n"
            "eletronicos"
        )

    elif etapa == "categoria":
        categoria = normalizar_titulo(texto)

        aliases = {
            "eletronico": "eletronicos",
            "eletronicos": "eletronicos",
            "utilidade": "utilidades",
            "ferramenta": "ferramentas",
        }

        categoria = aliases.get(categoria, categoria)

        if categoria not in CATEGORIAS_VALIDAS:
            responder_usuario(
                chat_id,
                "Categoria inválida. Responda: utilidades, "
                "ferramentas ou eletronicos."
            )
            return

        finalizar_cadastro_manual(
            chat_id,
            user_id,
            sessao,
            categoria,
        )

        SESSOES_ML.pop(user_id, None)


# ==================================================
# COMANDOS TELEGRAM
# ==================================================

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
        responder_usuario(
            chat_id,
            f"Seu ID do Telegram é: {user_id}"
        )
        return

    if chat.get("type") != "private":
        return

    if not TELEGRAM_ADMIN_ID or user_id != TELEGRAM_ADMIN_ID:
        if comando in (
            "/sh", "/ml", "/start", "/ajuda",
            "/cancelar"
        ):
            responder_usuario(
                chat_id,
                "Acesso restrito ao administrador do robô."
            )
        return

    if comando == "/cancelar":
        SESSOES_ML.pop(user_id, None)
        responder_usuario(chat_id, "Cadastro cancelado.")
        return

    if comando == "/start":
        responder_usuario(
            chat_id,
            "🤖 ROBÔ DE OFERTAS\n\n"
            "Comandos disponíveis:\n\n"
            "/sh — Buscar ofertas na Shopee\n"
            "/ml — Cadastrar oferta manual\n"
            "/ajuda — Ver comandos\n"
            "/id — Consultar seu ID\n"
            "/cancelar — Cancelar cadastro\n\n"
            "A busca da Shopee só acontece quando você envia /sh."
        )
        return

    if comando == "/ajuda":
        responder_usuario(
            chat_id,
            "📌 COMANDOS DO ROBÔ\n\n"
            "/sh — Pesquisa Shopee e publica até 2 ofertas no total.\n"
            "/ml — Cadastro manual em 5 marketplaces.\n"
            "/cancelar — Cancela o cadastro atual.\n"
            "/id — Mostra seu ID do Telegram.\n"
            "/start — Mostra este menu.\n\n"
            "Não existem publicações automáticas agendadas."
        )
        return

    if comando == "/sh":
        if SESSOES_ML.get(user_id):
            responder_usuario(
                chat_id,
                "Você tem um cadastro manual em andamento. "
                "Envie /cancelar antes de iniciar o /sh."
            )
            return

        if EXECUCAO_LOCK.locked():
            responder_usuario(
                chat_id,
                "Já existe uma execução em andamento."
            )
            return

        responder_usuario(
            chat_id,
            "🔎 Iniciando busca Shopee.\n"
            "Vou pesquisar as três categorias e selecionar "
            "até duas ofertas no total."
        )

        def executar_em_background():
            sucesso, mensagem_final = executar_sh()
            responder_usuario(
                chat_id,
                ("✅ " if sucesso else "⚠️ ") + mensagem_final
            )

        threading.Thread(
            target=executar_em_background,
            daemon=True,
        ).start()

        return

    if comando == "/ml":
        iniciar_cadastro_ml(chat_id, user_id)
        return

    sessao = SESSOES_ML.get(user_id)

    if sessao:
        processar_etapa_manual(
            chat_id,
            user_id,
            texto,
            sessao,
        )


# ==================================================
# WEBHOOK
# ==================================================

def segredo_webhook():
    if TELEGRAM_WEBHOOK_SECRET:
        return TELEGRAM_WEBHOOK_SECRET

    return hashlib.sha256(
        (TELEGRAM_TOKEN + ":telegram-webhook").encode()
    ).hexdigest()


def configurar_webhook():
    if not TELEGRAM_TOKEN:
        logging.error("TELEGRAM_TOKEN ausente.")
        return

    host = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")

    if not host:
        logging.error("RENDER_EXTERNAL_URL ausente.")
        return

    url = f"{host}/telegram/webhook"

    try:
        resposta = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            json={
                "url": url,
                "secret_token": segredo_webhook(),
                "allowed_updates": ["message"],
                "drop_pending_updates": False,
            },
            timeout=20,
        )

        resposta.raise_for_status()
        dados = resposta.json()

        if dados.get("ok"):
            logging.info("Webhook configurado: %s", url)
        else:
            logging.error(
                "Telegram recusou webhook: %s",
                dados.get("description"),
            )

    except Exception:
        logging.exception("Erro ao configurar webhook.")


# ==================================================
# SERVIDOR HTTP
# ==================================================

class HealthHandler(BaseHTTPRequestHandler):

    def responder(self, status, corpo=b"ok"):
        self.send_response(status)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        caminho = urlparse(self.path)

        if caminho.path == "/":
            self.responder(200, b"Robo de ofertas ativo")
        else:
            self.responder(404, b"Rota nao encontrada")

    def do_POST(self):
        if urlparse(self.path).path != "/telegram/webhook":
            self.responder(404, b"Rota nao encontrada")
            return

        recebido = self.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            "",
        )

        if not secrets.compare_digest(
            recebido,
            segredo_webhook(),
        ):
            self.responder(403, b"forbidden")
            return

        try:
            tamanho = int(self.headers.get("Content-Length", "0"))

            if tamanho <= 0 or tamanho > 1024 * 1024:
                self.responder(400, b"invalid body")
                return

            corpo = self.rfile.read(tamanho)
            update = json.loads(corpo.decode("utf-8"))

            # Responde imediatamente ao Telegram.
            self.responder(200, b"ok")

            # Processa a mensagem fora da requisição HTTP.
            threading.Thread(
                target=processar_mensagem_telegram,
                args=(update.get("message"),),
                daemon=True,
            ).start()

        except Exception:
            logging.exception("Falha no webhook.")

            try:
                self.responder(500, b"error")
            except Exception:
                pass

    def log_message(self, formato, *args):
        return


def run_web_server():
    porta = int(os.environ.get("PORT", "10000"))

    servidor = ThreadingHTTPServer(
        ("0.0.0.0", porta),
        HealthHandler,
    )

    logging.info("Servidor HTTP iniciado na porta %s.", porta)
    servidor.serve_forever()


# ==================================================
# INICIALIZAÇÃO
# ==================================================

if __name__ == "__main__":

    if not TELEGRAM_TOKEN:
        logging.error("TELEGRAM_TOKEN não configurado.")

    if not TELEGRAM_ADMIN_ID:
        logging.warning("TELEGRAM_ADMIN_ID não configurado.")

    threading.Thread(
        target=run_web_server,
        daemon=True,
    ).start()

    time.sleep(2)

    configurar_webhook()

    logging.info(
        "Robô iniciado. Sem agendamentos automáticos. "
        "Shopee somente por /sh."
    )

    # Mantém o processo ativo no Render.
    while True:
        time.sleep(60)
