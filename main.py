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

from datetime import datetime, timedelta

from zoneinfo import ZoneInfo

from http.server import HTTPServer, BaseHTTPRequestHandler

from urllib.parse import urlencode, urlparse, parse_qs

from shopee_api import buscar_ofertas

# ==================================================

# CONFIGURAÇÕES

# ==================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()

# O histórico permanente fica no Supabase. A chave secreta deve existir

# somente nas variáveis de ambiente do Render, nunca no código.

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

# Horários locais de Brasília (America/Sao_Paulo)

HORARIOS_PUBLICACAO = [(6, 30), (11, 0), (19, 0)]

INTERVALO_VERIFICACAO = 20     # verifica a agenda a cada 20 segundos

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

# CONTROLE DE PUBLICAÇÕES E DUPLICATAS

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

                                logging.exception("Não foi possível ler o estado do robô.")

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

def normalizar_titulo(titulo):

                """Normaliza o título para reconhecer anúncios equivalentes."""

                texto = unicodedata.normalize("NFKD", str(titulo or ""))

                texto = "".join(c for c in texto if not unicodedata.combining(c))

                texto = texto.lower()

                texto = re.sub(r"[^a-z0-9]+", " ", texto)

                return " ".join(texto.split())

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

                """Carrega todo o histórico; falha fechada se o banco estiver indisponível."""

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

                                                raise RuntimeError("Resposta inesperada ao consultar o histórico do Supabase.")

                                for linha in linhas:

                                                if linha.get("id_shopee"):

                                                                ids.add(str(linha["id_shopee"]))

                                                if linha.get("titulo_normalizado"):

                                                                titulos.add(linha["titulo_normalizado"])

                                if len(linhas) < 1000:

                                                break

                                inicio += len(linhas)

                logging.info(

                                "Histórico Supabase carregado: %s IDs e %s títulos.",

                                len(ids), len(titulos),

                )

                return ids, titulos

def reservar_no_supabase(chave, titulo, origem="shopee"):

                """Registra a oferta antes do envio para evitar duplicação após reinício."""

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

                # Com ignore-duplicates, conflito por ID ou título retorna uma lista vazia.

                return isinstance(resultado, list) and len(resultado) > 0

def importar_historico_local(estado):

                """Copia publicações já registradas no arquivo local para o histórico permanente."""

                for publicacao in estado.get("publicacoes", []):

                                chave = publicacao.get("id")

                                titulo = publicacao.get("titulo", "")

                                if not chave:

                                                continue

                                try:

                                                url = f"{SUPABASE_URL}/rest/v1/produtos_publicados"

                                                resposta = requests.post(

                                                                url,

                                                                json={

                                                                                "id_shopee": str(chave),

                                                                                "titulo": str(titulo or ""),

                                                                                "titulo_normalizado": normalizar_titulo(titulo),

                                                                },

                                                                headers=supabase_headers("resolution=ignore-duplicates,return=minimal"),

                                                                timeout=SUPABASE_TIMEOUT,

                                                )

                                                resposta.raise_for_status()

                                except Exception:

                                                logging.exception("Não foi possível importar uma publicação antiga para o Supabase.")

                                                raise

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

                # Nota e quantidade de avaliações, quando disponíveis.

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

                # Produtos bem avaliados podem entrar com desconto a partir de 15%.

                if (

                                avaliacao is not None

                                and avaliacao >= 4.5

                                and total_avaliacoes is not None

                                and total_avaliacoes >= 20

                ):

                                minimo = 15

                # Não aprovar produtos mal avaliados com histórico suficiente.

                if (

                                avaliacao is not None

                                and avaliacao < 4.0

                                and total_avaliacoes is not None

                                and total_avaliacoes >= 20

                ):

                                logging.info(

                                                "Produto rejeitado por avaliação baixa: %s | Nota: %s",

                                                titulo,

                                                avaliacao,

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

# BUSCA DE OFERTAS

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

                                "Motor para Aspirador",

                                "Puxador Porta Geladeira",

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

                def numero_seguro(valor):

                                try:

                                                return float(valor or 0)

                                except (TypeError, ValueError):

                                                return 0.0

                def normalizar(texto):

                                texto = str(texto or "").lower()

                                texto = unicodedata.normalize("NFKD", texto)

                                return "".join(

                                                caractere

                                                for caractere in texto

                                                if not unicodedata.combining(caractere)

                                )

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

                                                                                titulo = normalizar(produto.get("titulo", ""))

                                                                                # Bloqueia produtos infantis em ferramentas e eletronicos.

                                                                                if (

                                                                                                categoria in ("ferramentas", "eletronicos")

                                                                                                and any(item in titulo for item in termos_infantis)

                                                                                ):

                                                                                                logging.info(

                                                                                                                "Produto infantil ignorado em %s: %s",

                                                                                                                categoria,

                                                                                                                produto.get("titulo", ""),

                                                                                                )

                                                                                                continue

                                                                                # Bloqueia itens de reposicao e outros produtos especificos.

                                                                                if any(

                                                                                                item in titulo for item in termos_bloqueados

                                                                                ):

                                                                                                logging.info(

                                                                                                                "Produto ignorado por filtro: %s",

                                                                                                                produto.get("titulo", ""),

                                                                                                )

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

                # Prioriza vendas, depois avaliacao e por fim desconto.

                candidatos.sort(

                                key=lambda produto: (

                                                numero_seguro(produto.get("vendas")),

                                                numero_seguro(produto.get("avaliacao")),

                                                numero_seguro(produto.get("_desconto_numero")),

                                ),

                                reverse=True,

                )

                logging.info(

                                "Total de candidatos aprovados após filtros: %s",

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

                titulo = str(produto.get("titulo") or "Oferta especial").strip()

                preco = produto["_preco_numero"]

                desconto = produto["_desconto_numero"]

                link = str(produto.get("link_afiliado") or "").strip()

                foto = str(produto.get("foto") or "").strip()

                def formatar_preco(valor):

                                return (

                                                f"R$ {valor:,.2f}"

                                                .replace(",", "X")

                                                .replace(".", ",")

                                                .replace("X", ".")

                                )

                # Encurta títulos muito longos sem cortar palavras.

                limite_titulo = 100

                if len(titulo) > limite_titulo:

                                titulo_curto = titulo[:limite_titulo].rsplit(" ", 1)[0]

                                titulo = titulo_curto.rstrip(" ,.-") + "..."

                preco_formatado = formatar_preco(preco)

                preco_original = None

                if 0 < desconto < 100 and preco > 0:

                                preco_original = preco / (1 - desconto / 100)

                if preco_original and preco_original > preco:

                                linha_preco = (

                                                f"💸 <s>De: {formatar_preco(preco_original)}</s>\n"

                                                f"🔥 <b>Por: {preco_formatado}</b>\n"

                                )

                else:

                                linha_preco = f"🔥 <b>Preço: {preco_formatado}</b>\n"

                mensagem = (

                                f"🔥 <b>OFERTA ENCONTRADA!</b>\n\n"

                                f"📦 <b>{titulo}</b>\n\n"

                                f"{linha_preco}"

                                f"🏷️ <b>{desconto:.0f}% OFF</b>\n\n"

        + f"\n\n🛒 <a href=\"{link}\">COMPRAR AGORA</a>"

                                f"<i>Preço original estimado com base no desconto anunciado. "

                                f"Confira o valor atual no anúncio.</i>"

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

                                                                "Telegram recusou a publicação: %s",

                                                                dados.get("description", "erro desconhecido"),

                                                )

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

def executar_ciclo(horario_slot):

                """Executa um lote consultando o histórico permanente antes de publicar."""

                estado = carregar_estado()

                chave_slot = f"{data_local()}|{horario_slot}"

                # Se não conseguirmos consultar o banco, não publicamos nada.

                try:

                                importar_historico_local(estado)

                                ids_publicados, titulos_publicados = carregar_historico_supabase()

                except Exception:

                                logging.exception(

                                                "Ciclo cancelado por falha no Supabase. Nenhuma oferta será publicada."

                                )

                                return

                publicados = publicacoes_hoje(estado)

                restantes = MAX_POSTS_DIA - len(publicados)

                if restantes <= 0:

                                logging.info("Limite diário de publicações atingido.")

                                estado.setdefault("execucoes_agendadas", {})[chave_slot] = "limite_diario"

                                salvar_estado(estado)

                                return

                candidatos = buscar_candidatos()

                vistos = set(estado.get("vistos", []))

                contagem_categoria_slot = {}

                for publicacao in publicados:

                                if publicacao.get("horario_slot") != horario_slot:

                                                continue

                                categoria = publicacao.get("categoria")

                                contagem_categoria_slot[categoria] = contagem_categoria_slot.get(categoria, 0) + 1

                total_enviados = 0

                for produto in candidatos:

                                if total_enviados >= restantes:

                                                break

                                categoria = produto["_categoria"]

                                chave = str(produto["_chave"])

                                titulo = produto.get("titulo", "")

                                titulo_normalizado = normalizar_titulo(titulo)

                                logging.info("DIAGNOSTICO | Produto: %s | Chave: %s", titulo, chave)

                                if chave in vistos or chave in ids_publicados:

                                                logging.info("Oferta ignorada: ID já registrado no histórico.")

                                                continue

                                if titulo_normalizado and titulo_normalizado in titulos_publicados:

                                                logging.info("Oferta ignorada: título equivalente já registrado: %s", titulo)

                                                continue

                                if contagem_categoria_slot.get(categoria, 0) >= MAX_POR_CATEGORIA:

                                                continue

                                # Reserva o ID/título no banco ANTES de enviar. Isso evita que um

                                # reinício entre o envio e o registro gere uma publicação duplicada.

                                try:

                                                reservada = reservar_no_supabase(chave, titulo)

                                except Exception:

                                                logging.exception("Falha ao registrar a oferta no Supabase; ciclo interrompido.")

                                                return

                                if not reservada:

                                                logging.info("Oferta ignorada: outro registro já possui esse ID ou título.")

                                                ids_publicados.add(chave)

                                                if titulo_normalizado:

                                                                titulos_publicados.add(titulo_normalizado)

                                                continue

                                # Mantemos a reserva mesmo se o Telegram retornar erro/timeout: em caso

                                # de resposta incerta, é mais seguro evitar uma possível republicação.

                                if enviar_telegram(produto, categoria):

                                                agora = datetime.now(FUSO).isoformat()

                                                estado["publicacoes"].append({

                                                                "data": data_local(),

                                                                "data_hora": agora,

                                                                "horario_slot": horario_slot,

                                                                "categoria": categoria,

                                                                "id": chave,

                                                                "titulo": titulo,

                                                                "preco": produto["_preco_numero"],

                                                                "desconto_anunciado": produto["_desconto_numero"],

                                                })

                                                vistos.add(chave)

                                                estado["vistos"] = list(vistos)[-5000:]

                                                contagem_categoria_slot[categoria] = contagem_categoria_slot.get(categoria, 0) + 1

                                                total_enviados += 1

                                                salvar_estado(estado)

                                                ids_publicados.add(chave)

                                                if titulo_normalizado:

                                                                titulos_publicados.add(titulo_normalizado)

                                                time.sleep(3)

                                else:

                                                logging.warning(

                                                                "Oferta reservada no Supabase, mas o envio não foi confirmado. "

                                                                "Ela não será repetida automaticamente para evitar duplicação."

                                                )

                                                ids_publicados.add(chave)

                                                if titulo_normalizado:

                                                                titulos_publicados.add(titulo_normalizado)

                estado.setdefault("execucoes_agendadas", {})[chave_slot] = "concluido"

                execucoes = estado["execucoes_agendadas"]

                if len(execucoes) > 60:

                                chaves_recentes = sorted(execucoes.keys())[-60:]

                                estado["execucoes_agendadas"] = {chave: execucoes[chave] for chave in chaves_recentes}

                salvar_estado(estado)

                logging.info(

                                "Lote %s finalizado. Publicações realizadas: %s",

                                horario_slot, total_enviados,

                )


# ==================================================
# CADASTRO MANUAL DE OFERTAS DO MERCADO LIVRE
# ==================================================

SESSOES_ML = {}
EXECUCAO_MANUAL_LOCK = threading.Lock()
CATEGORIAS_VALIDAS = ("utilidades", "ferramentas", "eletronicos")


def formatar_reais(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def responder_usuario(chat_id, texto):
    if not TELEGRAM_TOKEN:
        return
    try:
        resposta = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": texto, "disable_web_page_preview": True},
            timeout=20,
        )
        resposta.raise_for_status()
    except Exception:
        logging.exception("Não foi possível responder ao usuário no Telegram.")


def enviar_oferta_ml(produto, categoria):
    canal = CANAIS.get(categoria)
    if not TELEGRAM_TOKEN or not canal:
        logging.error("Canal do Telegram não configurado para %s.", categoria)
        return False

    titulo = html.escape(produto["titulo"])
    link = html.escape(produto["link_afiliado"], quote=True)
    preco_atual = produto["preco_atual"]
    preco_anterior = produto["preco_anterior"]
    linhas_preco = []
    if preco_anterior and preco_anterior > preco_atual:
        linhas_preco.append(f"💸 <s>De: {formatar_reais(preco_anterior)}</s>")
    linhas_preco.append(f"🔥 <b>Por: {formatar_reais(preco_atual)}</b>")
    desconto = 0
    if preco_anterior and preco_anterior > preco_atual:
        desconto = round((1 - preco_atual / preco_anterior) * 100)

    mensagem = (
        "🔥 <b>OFERTA DO MERCADO LIVRE!</b>\n\n"
        f"📦 <b>{titulo}</b>\n\n"
        + "\n".join(linhas_preco)
        + (f"\n🏷️ <b>{desconto}% OFF</b>" if desconto > 0 else "")
        + f"\n\n🛒 <a href=\"{link}\">COMPRAR AGORA</a>"
        + "\n\n<i>Preço e disponibilidade podem mudar. Confira o anúncio.</i>"
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
        if not resposta.json().get("ok"):
            logging.error("Telegram recusou a oferta manual do Mercado Livre.")
            return False
        return True
    except Exception:
        logging.exception("Falha ao publicar oferta manual do Mercado Livre.")
        return False


def iniciar_cadastro_ml(chat_id, user_id):
    SESSOES_ML[str(user_id)] = {"chat_id": chat_id, "etapa": "link"}
    responder_usuario(
        chat_id,
        "Vamos cadastrar sua oferta do Mercado Livre!\n"
        "1/5 — Envie o link oficial de afiliado (ou /cancelar para sair)."
    )


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

    comando_inicial = texto.split()[0].split("@")[0].lower()
    if chat.get("type") == "private" and comando_inicial == "/id":
        responder_usuario(chat_id, f"Seu ID do Telegram é: {user_id}")
        return

    # Só aceitar comandos no privado, e somente do administrador configurado.
    if chat.get("type") != "private":
        return
    if not TELEGRAM_ADMIN_ID or user_id != TELEGRAM_ADMIN_ID:
        if texto.startswith("/ml"):
            responder_usuario(chat_id, "Cadastro restrito ao administrador do robô.")
        return

    comando = texto.split()[0].split("@")[0].lower()
    if comando == "/cancelar":
        SESSOES_ML.pop(user_id, None)
        responder_usuario(chat_id, "Cadastro cancelado.")
        return
    if comando == "/ml":
        iniciar_cadastro_ml(chat_id, user_id)
        return
    if comando == "/start":
        if EXECUCAO_MANUAL_LOCK.locked():
            responder_usuario(chat_id, "A busca da Shopee já está em andamento. Aguarde a conclusão.")
            return
        responder_usuario(chat_id, "🚀 Recebi seu comando! Estou iniciando agora a busca de ofertas da Shopee. Você receberá as ofertas nos canais configurados.\n\n/ml — cadastrar oferta do Mercado Livre")
        def executar_manual():
            if not EXECUCAO_MANUAL_LOCK.acquire(blocking=False):
                return
            try:
                executar_ciclo("manual")
                responder_usuario(chat_id, "✅ Busca manual da Shopee finalizada. Confira os canais e os logs do Render.")
            except Exception:
                logging.exception("Falha na busca manual solicitada pelo Telegram.")
                responder_usuario(chat_id, "⚠️ Ocorreu um erro durante a busca da Shopee. Confira os logs do Render.")
            finally:
                EXECUCAO_MANUAL_LOCK.release()
        threading.Thread(target=executar_manual, daemon=True).start()
        return
    if comando == "/ajuda":
        responder_usuario(chat_id, "Comandos disponíveis:\n/start — iniciar busca manual da Shopee\n/ml — cadastrar oferta manual do Mercado Livre\n/cancelar — cancelar cadastro")
        return

    sessao = SESSOES_ML.get(user_id)
    if not sessao:
        return

    etapa = sessao["etapa"]
    if etapa == "link":
        host = (urlparse(texto).hostname or "").lower()
        dominios_validos = ("mercadolivre.com.br", "mercadolivre.com", "mercadolibre.com", "meli.la")
        if urlparse(texto).scheme not in ("https", "http") or not any(
            host == dominio or host.endswith("." + dominio)
            for dominio in dominios_validos
        ):
            responder_usuario(chat_id, "Esse link não parece ser do Mercado Livre. Envie o link oficial de afiliado.")
            return
        sessao["link_afiliado"] = texto
        sessao["etapa"] = "titulo"
        responder_usuario(chat_id, "2/5 — Envie o nome do produto.")
    elif etapa == "titulo":
        if len(texto) < 3 or len(texto) > 180:
            responder_usuario(chat_id, "Envie um título entre 3 e 180 caracteres.")
            return
        sessao["titulo"] = texto
        sessao["etapa"] = "preco_atual"
        responder_usuario(chat_id, "3/5 — Qual é o preço atual? Exemplo: 149,90")
    elif etapa == "preco_atual":
        valor = numero(texto)
        if valor is None or valor <= 0:
            responder_usuario(chat_id, "Preço inválido. Envie somente um valor positivo, por exemplo 149,90.")
            return
        sessao["preco_atual"] = valor
        sessao["etapa"] = "preco_anterior"
        responder_usuario(chat_id, "4/5 — Qual era o preço anterior? Envie 0 se não houver preço anterior.")
    elif etapa == "preco_anterior":
        valor = numero(texto)
        if valor is None or valor < 0:
            responder_usuario(chat_id, "Preço inválido. Envie um valor positivo ou 0.")
            return
        if valor > 0 and valor <= sessao["preco_atual"]:
            responder_usuario(chat_id, "O preço anterior precisa ser maior que o atual. Envie novamente ou 0 se não houver.")
            return
        sessao["preco_anterior"] = valor or 0
        sessao["etapa"] = "categoria"
        responder_usuario(
            chat_id,
            "5/5 — Em qual canal devo publicar? Responda com uma opção: "
            "utilidades, ferramentas ou eletronicos."
        )
    elif etapa == "categoria":
        categoria = texto.lower().strip()
        aliases = {"eletrônicos": "eletronicos", "eletronico": "eletronicos", "utilidade": "utilidades", "ferramenta": "ferramentas"}
        categoria = aliases.get(categoria, categoria)
        if categoria not in CATEGORIAS_VALIDAS:
            responder_usuario(chat_id, "Opção inválida. Responda: utilidades, ferramentas ou eletronicos.")
            return
        produto = {
            "titulo": sessao["titulo"],
            "link_afiliado": sessao["link_afiliado"],
            "preco_atual": sessao["preco_atual"],
            "preco_anterior": sessao["preco_anterior"],
        }
        if not CANAIS.get(categoria):
            responder_usuario(chat_id, f"O canal '{categoria}' não está configurado no Render. Cadastro não publicado.")
            SESSOES_ML.pop(user_id, None)
            return

        chave = "mercadolivre:" + hashlib.sha256(sessao["link_afiliado"].encode("utf-8")).hexdigest()[:32]
        try:
            ids, titulos = carregar_historico_supabase()
            titulo_normalizado = normalizar_titulo(produto["titulo"])
            if chave in ids or (titulo_normalizado and titulo_normalizado in titulos):
                responder_usuario(chat_id, "Essa oferta ou um título equivalente já consta no histórico do Supabase. Não publiquei para evitar duplicação.")
                SESSOES_ML.pop(user_id, None)
                return
            reservada = reservar_no_supabase(chave, produto["titulo"], origem="mercadolivre")
            if not reservada:
                responder_usuario(chat_id, "O Supabase identificou possível duplicata. A oferta não foi publicada.")
                SESSOES_ML.pop(user_id, None)
                return
        except Exception:
            logging.exception("Falha ao registrar a oferta manual no Supabase.")
            responder_usuario(chat_id, "Não consegui consultar/registrar o histórico no Supabase. Não publiquei a oferta.")
            SESSOES_ML.pop(user_id, None)
            return

        if enviar_oferta_ml(produto, categoria):
            estado = carregar_estado()
            estado.setdefault("publicacoes", []).append({
                "data": data_local(),
                "data_hora": datetime.now(FUSO).isoformat(),
                "horario_slot": "manual",
                "categoria": categoria,
                "id": chave,
                "titulo": produto["titulo"],
                "preco": produto["preco_atual"],
                "origem": "mercadolivre",
            })
            salvar_estado(estado)
            responder_usuario(chat_id, "✅ Oferta publicada e registrada no histórico!")
        else:
            responder_usuario(chat_id, "O envio ao canal falhou. A reserva ficou no histórico para evitar uma possível publicação duplicada; confira os logs do Render.")
        SESSOES_ML.pop(user_id, None)


def configurar_webhook():
    """Registra o webhook do Telegram apontando para este serviço Render."""
    if not TELEGRAM_TOKEN:
        logging.error("Webhook não configurado: TELEGRAM_TOKEN ausente.")
        return
    host = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if not host:
        logging.error("Webhook não configurado: RENDER_EXTERNAL_URL ausente no ambiente Render.")
        return
    segredo = TELEGRAM_WEBHOOK_SECRET or hashlib.sha256((TELEGRAM_TOKEN + ":telegram-webhook").encode()).hexdigest()
    url = f"{host}/telegram/webhook"
    try:
        resposta = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            json={"url": url, "secret_token": segredo, "allowed_updates": ["message"], "drop_pending_updates": False},
            timeout=20,
        )
        resposta.raise_for_status()
        dados = resposta.json()
        if dados.get("ok"):
            logging.info("Webhook do Telegram configurado: %s", url)
        else:
            logging.error("Telegram recusou configurar webhook: %s", dados.get("description"))
    except Exception:
        logging.exception("Não foi possível configurar o webhook do Telegram.")


# MONITORAMENTO DO RENDER

# ==================================================

class HealthHandler(BaseHTTPRequestHandler):
    def _responder(self, status, corpo=b"ok"):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        caminho = urlparse(self.path)
        if caminho.path == "/":
            self._responder(200, b"Robo de ofertas ativo")
        else:
            self._responder(404, b"Rota nao encontrada")

    def do_POST(self):
        if urlparse(self.path).path != "/telegram/webhook":
            self._responder(404, b"Rota nao encontrada")
            return
        segredo = TELEGRAM_WEBHOOK_SECRET or hashlib.sha256((TELEGRAM_TOKEN + ":telegram-webhook").encode()).hexdigest()
        recebido = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not secrets.compare_digest(recebido, segredo):
            self._responder(403, b"forbidden")
            return
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
            if tamanho <= 0 or tamanho > 1024 * 1024:
                self._responder(400, b"invalid body")
                return
            update = json.loads(self.rfile.read(tamanho).decode("utf-8"))
            processar_mensagem_telegram(update.get("message"))
            self._responder(200, b"ok")
        except Exception:
            logging.exception("Falha ao processar atualização recebida pelo webhook.")
            self._responder(500, b"error")

    def log_message(self, format, *args):
        return


def run_web_server():

                porta = int(os.environ.get("PORT", "10000"))

                servidor = HTTPServer(("0.0.0.0", porta), HealthHandler)

                servidor.serve_forever()

def iniciar_robo():

                logging.info(

                                "Robô iniciado. Horários de publicação: 06:30, 11:00 e 19:00 (Brasília)."

                )

                while True:

                                try:

                                                agora = datetime.now(FUSO)

                                                estado = carregar_estado()

                                                execucoes = estado.setdefault("execucoes_agendadas", {})

                                                hoje = agora.date().isoformat()

                                                horarios_devidos = []

                                                for hora, minuto in HORARIOS_PUBLICACAO:

                                                                horario_slot = f"{hora:02d}:{minuto:02d}"

                                                                chave_slot = f"{hoje}|{horario_slot}"

                                                                instante_slot = agora.replace(

                                                                                hour=hora, minute=minuto, second=0, microsecond=0

                                                                )

                                                                if agora >= instante_slot and chave_slot not in execucoes:

                                                                                horarios_devidos.append((instante_slot, horario_slot, chave_slot))

                                                if horarios_devidos:

                                                                # Se o serviço ficou desligado por vários horários, executa

                                                                # apenas o lote mais recente para não publicar vários lotes juntos.

                                                                horarios_devidos.sort(key=lambda item: item[0])

                                                                for _, horario_antigo, chave_antiga in horarios_devidos[:-1]:

                                                                                execucoes[chave_antiga] = "ignorado_atraso"

                                                                if len(horarios_devidos) > 1:

                                                                                salvar_estado(estado)

                                                                _, horario_slot, _ = horarios_devidos[-1]

                                                                executar_ciclo(horario_slot)

                                                else:

                                                                time.sleep(INTERVALO_VERIFICACAO)

                                except Exception:

                                                logging.exception("Erro inesperado no agendador.")

                                                time.sleep(INTERVALO_VERIFICACAO)

if __name__ == "__main__":

                threading.Thread(

                                target=run_web_server,

                                daemon=True,

                ).start()

                # O recebimento das mensagens agora é feito pelo webhook HTTP.
                # Não iniciar ouvir_telegram: essa função não existe nesta versão.
                time.sleep(2)  # dá tempo para o servidor HTTP abrir a porta
                configurar_webhook()

                iniciar_robo()
