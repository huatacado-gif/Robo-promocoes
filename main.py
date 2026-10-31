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

MAX_POSTS_DIA = int(os.getenv("MAX_POSTS_DAY", "18"))
MAX_POR_CATEGORIA = 2

HORARIOS_PUBLICACAO = [(6, 30), (11, 0), (19, 0)]
INTERVALO_VERIFICACAO = 20

FUSO = ZoneInfo("America/Sao_Paulo")
ARQUIVO_ESTADO = Path("estado_robo.json")

# Um único bloqueio para impedir ciclos simultâneos.
EXECUCAO_LOCK = threading.Lock()

SESSOES_ML = {}
CATEGORIAS_VALIDAS = ("utilidades", "ferramentas", "eletronicos")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


# ==================================================
# CATEGORIAS
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
    texto = "".join(
        c for c in texto if not unicodedata.combining(c)
    )
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
# ARQUIVO DE ESTADO
# ==================================================

def carregar_estado():
    estado_padrao = {
        "publicacoes": [],
        "vistos": [],
        "execucoes_agendadas": {},
    }

    if not ARQUIVO_ESTADO.exists():
        return estado_padrao

    try:
        with ARQUIVO_ESTADO.open("r", encoding="utf-8") as arquivo:
            estado = json.load(arquivo)

        estado.setdefault("publicacoes", [])
        estado.setdefault("vistos", [])
        estado.setdefault("execucoes_agendadas", {})

        return estado

    except (OSError, json.JSONDecodeError):
        logging.exception("Erro ao carregar estado do robô.")
        return estado_padrao


def salvar_estado(estado):
    temporario = ARQUIVO_ESTADO.with_suffix(".tmp")

    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(
            estado,
            arquivo,
            ensure_ascii=False,
            indent=2,
        )

    temporario.replace(ARQUIVO_ESTADO)


def publicacoes_hoje(estado):
    hoje = data_local()

    return [
        p for p in estado.get("publicacoes", [])
        if p.get("data") == hoje
    ]


def contar_publicacoes_categoria(estado, categoria):
    return sum(
        1 for p in publicacoes_hoje(estado)
        if p.get("categoria") == categoria
    )


def pode_publicar(estado, categoria):
    total_hoje = len(publicacoes_hoje(estado))

    if total_hoje >= MAX_POSTS_DIA:
        return False, "O limite diário de publicações foi atingido."

    total_categoria = contar_publicacoes_categoria(
        estado,
        categoria,
    )

    if total_categoria >= MAX_POR_CATEGORIA:
        return False, (
            f"O limite diário da categoria {categoria} "
            "foi atingido."
        )

    return True, ""


def registrar_publicacao(
    estado,
    chave,
    titulo,
    categoria,
    preco,
    origem,
    horario_slot,
    desconto=None,
):
    estado.setdefault("publicacoes", []).append({
        "data": data_local(),
        "data_hora": agora_local().isoformat(),
        "horario_slot": horario_slot,
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
            params={
                "select": "id_shopee,titulo_normalizado"
            },
            headers=headers,
            timeout=SUPABASE_TIMEOUT,
        )

        resposta.raise_for_status()
        linhas = resposta.json()

        if not isinstance(linhas, list):
            raise RuntimeError(
                "Resposta inesperada do Supabase."
            )

        for linha in linhas:
            if linha.get("id_shopee"):
                ids.add(str(linha["id_shopee"]))

            if linha.get("titulo_normalizado"):
                titulos.add(linha["titulo_normalizado"])

        if len(linhas) < 1000:
            break

        inicio += len(linhas)

    logging.info(
        "Histórico carregado: %s IDs e %s títulos.",
        len(ids),
        len(titulos),
    )

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

        origem = publicacao.get("origem", "shopee")

        resposta = requests.post(
            url,
            json={
                "id_shopee": str(chave),
                "titulo": str(titulo or ""),
                "titulo_normalizado": normalizar_titulo(titulo),
                "origem": origem,
            },
            headers=supabase_headers(
                "resolution=ignore-duplicates,return=minimal"
            ),
            timeout=SUPABASE_TIMEOUT,
        )

        resposta.raise_for_status()


# ==================================================
# VALIDAÇÃO DE PRODUTOS SHOPEE
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

    campos_avaliacoes = (
        "quantidade_avaliacoes",
        "total_avaliacoes",
        "numero_avaliacoes",
        "avaliacoes",
        "review_count",
    )

    for campo in campos_avaliacoes:
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
        logging.info(
            "Produto rejeitado por avaliação baixa: %s",
            titulo,
        )
        return False

    if desconto < minimo:
        return False

    produto["_preco_numero"] = preco
    produto["_desconto_numero"] = desconto
    produto["_avaliacao_numero"] = avaliacao
    produto["_total_avaliacoes"] = total_avaliacoes

    return True


# ==================================================
# BUSCA DE OFERTAS SHOPEE
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
        "lixa",
        "lixas",
        "lixa de unha",
        "lima de unha",
        "polidor de unha",
        "adesivo",
        "unha postica",
        "unhas postiças",
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
        texto = unicodedata.normalize(
            "NFKD",
            str(texto or "").lower(),
        )

        return "".join(
            c for c in texto
            if not unicodedata.combining(c)
        )

    def numero_seguro(valor):
        try:
            return float(valor or 0)
        except (TypeError, ValueError):
            return 0.0

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

                    titulo_original = produto.get("titulo", "")
                    titulo = normalizar(titulo_original)

                    if (
                        categoria in ("ferramentas", "eletronicos")
                        and any(item in titulo for item in termos_infantis)
                    ):
                        continue

                    if any(
                        item in titulo
                        for item in termos_bloqueados
                    ):
                        continue

                    # Evita bloquear celulares Motorola por conter
                    # a palavra "motor" no nome da marca.
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
                logging.exception(
                    "Erro ao buscar o termo: %s",
                    termo,
                )

    candidatos.sort(
        key=lambda produto: (
            numero_seguro(produto.get("vendas")),
            numero_seguro(produto.get("avaliacao")),
            numero_seguro(produto.get("_desconto_numero")),
        ),
        reverse=True,
    )

    logging.info(
        "Total de candidatos aprovados: %s",
        len(candidatos),
    )

    return candidatos


# ==================================================
# ENVIO DE OFERTAS SHOPEE
# ==================================================

def enviar_telegram(produto, categoria):
    canal = CANAIS.get(categoria)

    if not TELEGRAM_TOKEN or not canal:
        logging.error(
            "Token ou canal não configurado para %s.",
            categoria,
        )
        return False

    titulo_original = str(
        produto.get("titulo") or "Oferta especial"
    ).strip()

    titulo = html.escape(titulo_original)

    preco = produto["_preco_numero"]
    desconto = produto["_desconto_numero"]

    link_original = str(
        produto.get("link_afiliado") or ""
    ).strip()

    link = html.escape(link_original, quote=True)

    foto = str(produto.get("foto") or "").strip()

    if len(titulo) > 400:
        titulo = titulo[:397] + "..."

    preco_formatado = formatar_reais(preco)

    preco_original = None

    if 0 < desconto < 100 and preco > 0:
        preco_original = preco / (1 - desconto / 100)

    if preco_original and preco_original > preco:
        linha_preco = (
            f"💸 <s>De: {formatar_reais(preco_original)}</s>\n"
            f"🔥 <b>Por: {preco_formatado}</b>\n"
        )
    else:
        linha_preco = (
            f"🔥 <b>Preço: {preco_formatado}</b>\n"
        )

    mensagem = (
        "🔥 <b>OFERTA ENCONTRADA!</b>\n\n"
        f"📦 <b>{titulo}</b>\n\n"
        f"{linha_preco}"
        f"🏷️ <b>{desconto:.0f}% OFF</b>\n\n"
        f"🛒 <a href=\"{link}\">COMPRAR AGORA</a>\n\n"
        "<i>Preço original estimado com base no desconto anunciado. "
        "Confira o valor atual no anúncio.</i>"
    )

    base_url = (
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    )

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
            logging.error(
                "Telegram recusou publicação: %s",
                dados.get("description"),
            )
            return False

        logging.info(
            "Oferta publicada em %s: %s",
            categoria,
            titulo_original,
        )

        return True

    except Exception:
        logging.exception("Falha ao publicar no Telegram.")
        return False


# ==================================================
# CICLO PRINCIPAL
# ==================================================

def executar_ciclo(horario_slot):
    estado = carregar_estado()

    chave_slot = f"{data_local()}|{horario_slot}"

    try:
        importar_historico_local(estado)
        ids_publicados, titulos_publicados = (
            carregar_historico_supabase()
        )

    except Exception:
        logging.exception(
            "Ciclo cancelado por falha no Supabase."
        )
        return

    publicados = publicacoes_hoje(estado)
    restantes = MAX_POSTS_DIA - len(publicados)

    if restantes <= 0:
        logging.info("Limite diário atingido.")

        estado.setdefault(
            "execucoes_agendadas", {}
        )[chave_slot] = "limite_diario"

        salvar_estado(estado)
        return

    candidatos = buscar_candidatos()

    vistos = set(estado.get("vistos", []))

    contagem_categoria_slot = {}

    for publicacao in publicados:
        if publicacao.get("horario_slot") != horario_slot:
            continue

        categoria = publicacao.get("categoria")

        contagem_categoria_slot[categoria] = (
            contagem_categoria_slot.get(categoria, 0) + 1
        )

    total_enviados = 0

    for produto in candidatos:
        if total_enviados >= restantes:
            break

        categoria = produto["_categoria"]
        chave = str(produto["_chave"])
        titulo = produto.get("titulo", "")

        titulo_normalizado = normalizar_titulo(titulo)

        if chave in vistos or chave in ids_publicados:
            continue

        if (
            titulo_normalizado
            and titulo_normalizado in titulos_publicados
        ):
            continue

        if (
            contagem_categoria_slot.get(categoria, 0)
            >= MAX_POR_CATEGORIA
        ):
            continue

        try:
            reservada = reservar_no_supabase(
                chave,
                titulo,
                origem="shopee",
            )

        except Exception:
            logging.exception(
                "Falha ao reservar oferta no Supabase."
            )
            return

        if not reservada:
            logging.info(
                "Oferta ignorada por possível duplicidade: %s",
                titulo,
            )
            continue

        # A reserva é mantida mesmo se o envio falhar.
        # Isso evita uma possível publicação duplicada
        # quando o Telegram recebe a mensagem, mas a resposta
        # não chega ao robô.

        if enviar_telegram(produto, categoria):
            registrar_publicacao(
                estado=estado,
                chave=chave,
                titulo=titulo,
                categoria=categoria,
                preco=produto["_preco_numero"],
                origem="shopee",
                horario_slot=horario_slot,
                desconto=produto["_desconto_numero"],
            )

            contagem_categoria_slot[categoria] = (
                contagem_categoria_slot.get(categoria, 0) + 1
            )

            total_enviados += 1
            vistos.add(chave)
            ids_publicados.add(chave)

            if titulo_normalizado:
                titulos_publicados.add(titulo_normalizado)

            time.sleep(3)

        else:
            logging.warning(
                "Envio não confirmado. Reserva mantida: %s",
                titulo,
            )

    estado.setdefault(
        "execucoes_agendadas", {}
    )[chave_slot] = "concluido"

    execucoes = estado["execucoes_agendadas"]

    if len(execucoes) > 60:
        chaves_recentes = sorted(execucoes.keys())[-60:]

        estado["execucoes_agendadas"] = {
            chave: execucoes[chave]
            for chave in chaves_recentes
        }

    salvar_estado(estado)

    logging.info(
        "Lote %s finalizado. Publicações realizadas: %s",
        horario_slot,
        total_enviados,
    )


def executar_ciclo_protegido(horario_slot):
    if not EXECUCAO_LOCK.acquire(blocking=False):
        logging.warning(
            "Já existe uma publicação em andamento. "
            "Ciclo %s ignorado.",
            horario_slot,
        )
        return False

    try:
        executar_ciclo(horario_slot)
        return True

    except Exception:
        logging.exception("Erro inesperado no ciclo.")
        return False

    finally:
        EXECUCAO_LOCK.release()


# ==================================================
# CADASTRO MANUAL DE OFERTAS DO MERCADO LIVRE
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
        logging.exception(
            "Não foi possível responder ao usuário."
        )


def identificar_link_mercado_livre(link):
    try:
        parsed = urlparse(link)
        host = (parsed.hostname or "").lower()

        dominios_validos = (
            "mercadolivre.com.br",
            "mercadolivre.com",
            "mercadolibre.com",
            "meli.la",
        )

        if parsed.scheme != "https":
            return False

        return any(
            host == dominio
            or host.endswith("." + dominio)
            for dominio in dominios_validos
        )

    except Exception:
        return False


def enviar_oferta_ml(produto, categoria):
    canal = CANAIS.get(categoria)

    if not TELEGRAM_TOKEN or not canal:
        logging.error(
            "Canal não configurado para %s.",
            categoria,
        )
        return False

    titulo = html.escape(produto["titulo"])
    link = html.escape(
        produto["link_afiliado"],
        quote=True,
    )

    preco_atual = produto["preco_atual"]
    preco_anterior = produto["preco_anterior"]

    linhas_preco = []

    if preco_anterior and preco_anterior > preco_atual:
        linhas_preco.append(
            f"💸 <s>De: {formatar_reais(preco_anterior)}</s>"
        )

    linhas_preco.append(
        f"🔥 <b>Por: {formatar_reais(preco_atual)}</b>"
    )

    desconto = 0

    if preco_anterior and preco_anterior > preco_atual:
        desconto = round(
            (1 - preco_atual / preco_anterior) * 100
        )

    mensagem = (
        "🔥 <b>OFERTA DO MERCADO LIVRE!</b>\n\n"
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
            logging.error(
                "Telegram recusou oferta do Mercado Livre: %s",
                dados.get("description"),
            )
            return False

        logging.info(
            "Oferta do Mercado Livre publicada: %s",
            produto["titulo"],
        )

        return True

    except Exception:
        logging.exception(
            "Falha ao publicar oferta do Mercado Livre."
        )
        return False


def iniciar_cadastro_ml(chat_id, user_id):
    SESSOES_ML[str(user_id)] = {
        "chat_id": chat_id,
        "etapa": "link",
    }

    responder_usuario(
        chat_id,
        "Vamos cadastrar sua oferta!\n\n"
        "1/5 — Envie o link de afiliado do Mercado Livre.\n"
        "Digite /cancelar para sair.",
    )


def finalizar_cadastro_ml(chat_id, user_id, sessao, categoria):
    if not EXECUCAO_LOCK.acquire(blocking=False):
        responder_usuario(
            chat_id,
            "Já existe uma publicação em andamento. "
            "Aguarde alguns instantes e tente novamente.",
        )
        return

    try:
        estado = carregar_estado()

        permitido, motivo = pode_publicar(
            estado,
            categoria,
        )

        if not permitido:
            responder_usuario(chat_id, motivo)
            return

        produto = {
            "titulo": sessao["titulo"],
            "link_afiliado": sessao["link_afiliado"],
            "preco_atual": sessao["preco_atual"],
            "preco_anterior": sessao["preco_anterior"],
        }

        if not CANAIS.get(categoria):
            responder_usuario(
                chat_id,
                f"O canal '{categoria}' não está configurado no Render.",
            )
            return

        chave = (
            "mercadolivre:"
            + hashlib.sha256(
                sessao["link_afiliado"].encode("utf-8")
            ).hexdigest()[:32]
        )

        try:
            ids, titulos = carregar_historico_supabase()

            titulo_normalizado = normalizar_titulo(
                produto["titulo"]
            )

            if (
                chave in ids
                or (
                    titulo_normalizado
                    and titulo_normalizado in titulos
                )
            ):
                responder_usuario(
                    chat_id,
                    "Essa oferta ou um título equivalente já "
                    "consta no histórico. Não publiquei para "
                    "evitar duplicidade.",
                )
                return

            reservada = reservar_no_supabase(
                chave,
                produto["titulo"],
                origem="mercadolivre",
            )

            if not reservada:
                responder_usuario(
                    chat_id,
                    "O Supabase identificou possível duplicidade. "
                    "A oferta não foi publicada.",
                )
                return

        except Exception:
            logging.exception(
                "Falha ao registrar oferta manual no Supabase."
            )

            responder_usuario(
                chat_id,
                "Não consegui consultar ou registrar o histórico "
                "no Supabase. Não publiquei a oferta.",
            )
            return

        if enviar_oferta_ml(produto, categoria):
            registrar_publicacao(
                estado=estado,
                chave=chave,
                titulo=produto["titulo"],
                categoria=categoria,
                preco=produto["preco_atual"],
                origem="mercadolivre",
                horario_slot="manual",
            )

            responder_usuario(
                chat_id,
                "✅ Oferta publicada e registrada no histórico!",
            )

        else:
            responder_usuario(
                chat_id,
                "O envio ao canal falhou. A reserva ficou no "
                "histórico para evitar uma possível duplicação. "
                "Confira os logs do Render.",
            )

    finally:
        EXECUCAO_LOCK.release()


# ==================================================
# PROCESSAMENTO DAS MENSAGENS DO TELEGRAM
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
            f"Seu ID do Telegram é: {user_id}",
        )
        return

    if chat.get("type") != "private":
        return

    if not TELEGRAM_ADMIN_ID or user_id != TELEGRAM_ADMIN_ID:
        if comando in ("/ml", "/start", "/ajuda"):
            responder_usuario(
                chat_id,
                "Acesso restrito ao administrador do robô.",
            )
        return

    if comando == "/cancelar":
        SESSOES_ML.pop(user_id, None)

        responder_usuario(
            chat_id,
            "Cadastro cancelado.",
        )
        return

    if comando == "/ml":
        iniciar_cadastro_ml(chat_id, user_id)
        return

    if comando == "/start":
        if EXECUCAO_LOCK.locked():
            responder_usuario(
                chat_id,
                "Já existe uma execução em andamento. Aguarde.",
            )
            return

        responder_usuario(
            chat_id,
            "🚀 Iniciando busca manual de ofertas da Shopee.\n"
            "Você receberá as ofertas nos canais configurados.",
        )

        def executar_manual():
            resultado = executar_ciclo_protegido("manual")

            if resultado:
                responder_usuario(
                    chat_id,
                    "✅ Busca manual finalizada. "
                    "Confira os canais e os logs do Render.",
                )
            else:
                responder_usuario(
                    chat_id,
                    "⚠️ Não foi possível iniciar ou concluir "
                    "a busca. Confira os logs do Render.",
                )

        threading.Thread(
            target=executar_manual,
            daemon=True,
        ).start()

        return

    if comando == "/ajuda":
        responder_usuario(
            chat_id,
            "Comandos disponíveis:\n\n"
            "/start — iniciar busca manual da Shopee\n"
            "/ml — cadastrar oferta manual do Mercado Livre\n"
            "/cancelar — cancelar cadastro\n"
            "/id — consultar seu ID do Telegram",
        )
        return

    sessao = SESSOES_ML.get(user_id)

    if not sessao:
        return

    etapa = sessao["etapa"]

    if etapa == "link":
        if not identificar_link_mercado_livre(texto):
            responder_usuario(
                chat_id,
                "Esse link não parece ser do Mercado Livre. "
                "Envie um link HTTPS válido da plataforma.",
            )
            return

        sessao["link_afiliado"] = texto
        sessao["etapa"] = "titulo"

        responder_usuario(
            chat_id,
            "2/5 — Envie o nome do produto.",
        )

    elif etapa == "titulo":
        if len(texto) < 3 or len(texto) > 180:
            responder_usuario(
                chat_id,
                "Envie um título entre 3 e 180 caracteres.",
            )
            return

        sessao["titulo"] = texto
        sessao["etapa"] = "preco_atual"

        responder_usuario(
            chat_id,
            "3/5 — Qual é o preço atual?\n"
            "Exemplo: 149,90",
        )

    elif etapa == "preco_atual":
        valor = numero(texto)

        if valor is None or valor <= 0:
            responder_usuario(
                chat_id,
                "Preço inválido. Envie um valor positivo, "
                "por exemplo: 149,90.",
            )
            return

        sessao["preco_atual"] = valor
        sessao["etapa"] = "preco_anterior"

        responder_usuario(
            chat_id,
            "4/5 — Qual era o preço anterior?\n"
            "Envie 0 se não houver preço anterior.",
        )

    elif etapa == "preco_anterior":
        valor = numero(texto)

        if valor is None or valor < 0:
            responder_usuario(
                chat_id,
                "Preço inválido. Envie um valor positivo ou 0.",
            )
            return

        if valor > 0 and valor <= sessao["preco_atual"]:
            responder_usuario(
                chat_id,
                "O preço anterior precisa ser maior que o atual. "
                "Envie novamente ou 0 se não houver.",
            )
            return

        sessao["preco_anterior"] = valor
        sessao["etapa"] = "categoria"

        responder_usuario(
            chat_id,
            "5/5 — Em qual canal devo publicar?\n\n"
            "Responda com uma opção:\n"
            "utilidades\n"
            "ferramentas\n"
            "eletronicos",
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
                "Opção inválida. Responda:\n"
                "utilidades, ferramentas ou eletronicos.",
            )
            return

        finalizar_cadastro_ml(
            chat_id,
            user_id,
            sessao,
            categoria,
        )

        SESSOES_ML.pop(user_id, None)


# ==================================================
# WEBHOOK DO TELEGRAM
# ==================================================

def segredo_webhook():
    if TELEGRAM_WEBHOOK_SECRET:
        return TELEGRAM_WEBHOOK_SECRET

    return hashlib.sha256(
        (TELEGRAM_TOKEN + ":telegram-webhook").encode()
    ).hexdigest()


def configurar_webhook():
    if not TELEGRAM_TOKEN:
        logging.error(
            "Webhook não configurado: TELEGRAM_TOKEN ausente."
        )
        return

    host = os.getenv(
        "RENDER_EXTERNAL_URL",
        "",
    ).strip().rstrip("/")

    if not host:
        logging.error(
            "RENDER_EXTERNAL_URL ausente no Render."
        )
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
            logging.info(
                "Webhook configurado: %s",
                url,
            )
        else:
            logging.error(
                "Telegram recusou configurar webhook: %s",
                dados.get("description"),
            )

    except Exception:
        logging.exception(
            "Erro ao configurar webhook."
        )


# ==================================================
# SERVIDOR HTTP / MONITORAMENTO RENDER
# ==================================================

class HealthHandler(BaseHTTPRequestHandler):

    def responder(self, status, corpo=b"ok"):
        self.send_response(status)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )

        self.send_header(
            "Content-Length",
            str(len(corpo)),
        )

        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        caminho = urlparse(self.path)

        if caminho.path == "/":
            self.responder(
                200,
                b"Robo de ofertas ativo",
            )
        else:
            self.responder(
                404,
                b"Rota nao encontrada",
            )

    def do_POST(self):
        if urlparse(self.path).path != "/telegram/webhook":
            self.responder(
                404,
                b"Rota nao encontrada",
            )
            return

        recebido = self.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            "",
        )

        if not secrets.compare_digest(
            recebido,
            segredo_webhook(),
        ):
            self.responder(
                403,
                b"forbidden",
            )
            return

        try:
            tamanho = int(
                self.headers.get("Content-Length", "0")
            )

            if tamanho <= 0 or tamanho > 1024 * 1024:
                self.responder(
                    400,
                    b"invalid body",
                )
                return

            corpo = self.rfile.read(tamanho)

            update = json.loads(
                corpo.decode("utf-8")
            )

            processar_mensagem_telegram(
                update.get("message")
            )

            self.responder(
                200,
                b"ok",
            )

        except Exception:
            logging.exception(
                "Falha ao processar webhook."
            )

            self.responder(
                500,
                b"error",
            )

    def log_message(self, formato, *args):
        return


def run_web_server():
    porta = int(
        os.environ.get("PORT", "10000")
    )

    servidor = HTTPServer(
        ("0.0.0.0", porta),
        HealthHandler,
    )

    logging.info(
        "Servidor HTTP iniciado na porta %s.",
        porta,
    )

    servidor.serve_forever()


# ==================================================
# AGENDADOR
# ==================================================

def iniciar_robo():
    logging.info(
        "Robô iniciado. Horários: "
        "06:30, 11:00 e 19:00 (Brasília)."
    )

    while True:
        try:
            agora = agora_local()
            estado = carregar_estado()

            execucoes = estado.setdefault(
                "execucoes_agendadas",
                {},
            )

            hoje = agora.date().isoformat()
            horarios_devidos = []

            for hora, minuto in HORARIOS_PUBLICACAO:
                horario_slot = f"{hora:02d}:{minuto:02d}"

                chave_slot = (
                    f"{hoje}|{horario_slot}"
                )

                instante_slot = agora.replace(
                    hour=hora,
                    minute=minuto,
                    second=0,
                    microsecond=0,
                )

                if (
                    agora >= instante_slot
                    and chave_slot not in execucoes
                ):
                    horarios_devidos.append(
                        (
                            instante_slot,
                            horario_slot,
                            chave_slot,
                        )
                    )

            if horarios_devidos:
                horarios_devidos.sort(
                    key=lambda item: item[0]
                )

                # Se o serviço ficou desligado por vários
                # horários, ignora os antigos e executa
                # somente o lote mais recente.

                for _, horario_antigo, chave_antiga in (
                    horarios_devidos[:-1]
                ):
                    execucoes[chave_antiga] = (
                        "ignorado_atraso"
                    )

                if len(horarios_devidos) > 1:
                    salvar_estado(estado)

                _, horario_slot, _ = horarios_devidos[-1]

                executou = executar_ciclo_protegido(
                    horario_slot
                )

                if not executou:
                    time.sleep(INTERVALO_VERIFICACAO)

            else:
                time.sleep(INTERVALO_VERIFICACAO)

        except Exception:
            logging.exception(
                "Erro inesperado no agendador."
            )

            time.sleep(INTERVALO_VERIFICACAO)


# ==================================================
# INICIALIZAÇÃO
# ==================================================

if __name__ == "__main__":

    if not TELEGRAM_TOKEN:
        logging.error(
            "A variável TELEGRAM_TOKEN não está configurada."
        )

    if not TELEGRAM_ADMIN_ID:
        logging.warning(
            "TELEGRAM_ADMIN_ID não configurado."
        )

    threading.Thread(
        target=run_web_server,
        daemon=True,
    ).start()

    time.sleep(2)

    configurar_webhook()

    iniciar_robo()
