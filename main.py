import os
import json
import time
import logging
import threading
import requests
import unicodedata
import re

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import HTTPServer, BaseHTTPRequestHandler

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
        "nome": "Utilidades Domésticas",
        "desconto_minimo": 30,
        "termos": [
            "organizador de cozinha",
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
        "nome": "Eletrônicos e Eletrodomésticos",
        "desconto_minimo": 20,
        "termos": [
            "fone bluetooth",
            "smartwatch",
            "caixa de som bluetooth",
            "acessorios para celular",
            "smart tv",
            "televisao",
            "ar condicionado",
            "air fryer",
            "liquidificador",
            "ventilador",
            "aspirador de po",
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
        raise RuntimeError(
            "SUPABASE_URL ou SUPABASE_SECRET_KEY não configurada no Render."
        )
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
