import os
import json
import time
import logging
import threading
import requests
import unicodedata
import re
import secrets

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
INTERVALO_VERIFICACAO = 20  # verifica a agenda a cada 20 segundos
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


def reservar_no_supabase(chave, titulo):
    """Registra a oferta antes do envio para evitar duplicação após reinício."""
    url = f"{SUPABASE_URL}/rest/v1/produtos_publicados"
    registro = {
        "id_shopee": str(chave),
        "titulo": str(titulo or ""),
        "titulo_normalizado": normalizar_titulo(titulo),
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
        f"🛒 <a href=\"{link}\">COMPRAR AGORA</a>\n\n"
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
# MONITORAMENTO DO RENDER
# ==================================================

MELI_CLIENT_ID = os.getenv("MELI_CLIENT_ID", "").strip()
MELI_CLIENT_SECRET = os.getenv("MELI_CLIENT_SECRET", "").strip()

MELI_REDIRECT_URI = (
    "https://robo-promocoes-bgo6.onrender.com/"
    "oauth/mercadolivre/callback"
)

MELI_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
MELI_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

MELI_STATE_PENDENTE = None


def salvar_tokens_mercadolivre(dados):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Supabase não configurado.")

    access_token = dados.get("access_token")
    refresh_token = dados.get("refresh_token")

    if not access_token or not refresh_token:
        raise RuntimeError("Resposta de tokens incompleta.")

    expiracao = int(dados.get("expires_in", 21600))
    expires_at = datetime.now(
        ZoneInfo("UTC")
    ).timestamp() + expiracao

    payload = {
        "id": 1,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_id": dados.get("user_id"),
        "expires_at": datetime.fromtimestamp(
            expires_at, ZoneInfo("UTC")
        ).isoformat(),
        "atualizado_em": datetime.now(
            ZoneInfo("UTC")
        ).isoformat(),
    }

    resposta = requests.post(
        f"{SUPABASE_URL}/rest/v1/mercadolivre_tokens",
        params={"on_conflict": "id"},
        headers=supabase_headers(
            "resolution=merge-duplicates,return=minimal"
        ),
        json=payload,
        timeout=SUPABASE_TIMEOUT,
    )
    resposta.raise_for_status()


class HealthHandler(BaseHTTPRequestHandler):
    def responder(self, status, mensagem):
        corpo = mensagem.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        global MELI_STATE_PENDENTE

        caminho = urlparse(self.path)
        parametros = parse_qs(caminho.query)

        if caminho.path == "/":
            self.responder(200, "Robo de ofertas ativo")
            return

        if caminho.path == "/oauth/mercadolivre/start":
            if not MELI_CLIENT_ID or not MELI_CLIENT_SECRET:
                self.responder(500, "Configure as credenciais do Mercado Livre.")
                return

            MELI_STATE_PENDENTE = secrets.token_urlsafe(32)

            url = MELI_AUTH_URL + "?" + urlencode({
                "response_type": "code",
                "client_id": MELI_CLIENT_ID,
                "redirect_uri": MELI_REDIRECT_URI,
                "state": MELI_STATE_PENDENTE,
            })

            self.send_response(302)
            self.send_header("Location", url)
            self.end_headers()
            return

        if caminho.path == "/oauth/mercadolivre/callback":
            estado = parametros.get("state", [None])[0]
            codigo = parametros.get("code", [None])[0]
            erro = parametros.get("error", [None])[0]

            if erro:
                self.responder(400, "O Mercado Livre recusou ou cancelou a autorização.")
                return

            if not estado or estado != MELI_STATE_PENDENTE:
                self.responder(400, "Estado inválido ou expirado. Inicie a autorização novamente.")
                return

            if not codigo:
                self.responder(400, "Código de autorização não recebido.")
                return

            MELI_STATE_PENDENTE = None

            try:
                resposta = requests.post(
                    MELI_TOKEN_URL,
                    headers={"Accept": "application/json"},
                    data={
                        "grant_type": "authorization_code",
                        "client_id": MELI_CLIENT_ID,
                        "client_secret": MELI_CLIENT_SECRET,
                        "code": codigo,
                        "redirect_uri": MELI_REDIRECT_URI,
                    },
                    timeout=30,
                )

                try:
                    dados = resposta.json()
                except ValueError:
                    logging.error(
                        "Mercado Livre OAuth: resposta não JSON. HTTP %s",
                        resposta.status_code,
                    )
                    raise RuntimeError(
                        "Resposta inválida do Mercado Livre."
                    )

                if resposta.status_code >= 400:
                    logging.error(
                        "Mercado Livre OAuth: HTTP %s | erro=%s | mensagem=%s",
                        resposta.status_code,
                        dados.get("error", "não informado"),
                        dados.get("message", "sem mensagem"),
                    )
                    raise RuntimeError(
                        "Mercado Livre recusou a troca do código por tokens."
                    )

                campos_presentes = [
                    campo
                    for campo in (
                        "access_token",
                        "refresh_token",
                        "expires_in",
                        "user_id",
                    )
                    if dados.get(campo) is not None
                ]

                logging.info(
                    "Mercado Livre OAuth: campos recebidos: %s",
                    ", ".join(campos_presentes) or "nenhum",
                )

                salvar_tokens_mercadolivre(dados)

                logging.info("Autorização do Mercado Livre concluída.")
                self.responder(
                    200,
                    "Mercado Livre autorizado! Tokens salvos no Supabase."
                )

            except Exception:
                logging.exception("Falha na autorização do Mercado Livre.")
                self.responder(
                    500,
                    "Não foi possível concluir a autorização. Confira os logs do Render."
                )
            return

        self.responder(404, "Rota não encontrada.")

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

    iniciar_robo()
