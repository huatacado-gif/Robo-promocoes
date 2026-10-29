
import os
import logging
import threading
import requests

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()

MELI_CLIENT_ID = os.getenv("MELI_CLIENT_ID", "").strip()
MELI_CLIENT_SECRET = os.getenv("MELI_CLIENT_SECRET", "").strip()

TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
SEARCH_URL = "https://api.mercadolibre.com/sites/MLB/search"

TIMEOUT = 20

# Evita duas renovações simultâneas, pois o refresh_token é de uso único.
_token_lock = threading.Lock()


def _headers_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Supabase não configurado.")

    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def _ler_tokens():
    resposta = requests.get(
        f"{SUPABASE_URL}/rest/v1/mercadolivre_tokens",
        params={
            "id": "eq.1",
            "select": "access_token,refresh_token,expires_at,user_id",
        },
        headers=_headers_supabase(),
        timeout=TIMEOUT,
    )
    resposta.raise_for_status()

    linhas = resposta.json()
    if not linhas:
        raise RuntimeError(
            "Tokens do Mercado Livre não encontrados no Supabase. "
            "Faça a autorização novamente."
        )

    return linhas[0]


def _salvar_tokens(dados):
    access_token = dados.get("access_token")
    refresh_token = dados.get("refresh_token")

    if not access_token or not refresh_token:
        raise RuntimeError("Resposta de renovação sem os tokens necessários.")

    expiracao = int(dados.get("expires_in", 21600))
    agora = datetime.now(timezone.utc)

    payload = {
        "id": 1,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_id": dados.get("user_id"),
        "expires_at": (agora + timedelta(seconds=expiracao)).isoformat(),
        "atualizado_em": agora.isoformat(),
    }

    resposta = requests.post(
        f"{SUPABASE_URL}/rest/v1/mercadolivre_tokens",
        params={"on_conflict": "id"},
        headers={
            **_headers_supabase(),
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json=payload,
        timeout=TIMEOUT,
    )
    resposta.raise_for_status()


def obter_access_token():
    """Retorna um access token válido e renova-o se estiver perto de expirar."""
    with _token_lock:
        tokens = _ler_tokens()

        expiracao = tokens.get("expires_at")
        if expiracao:
            data_expiracao = datetime.fromisoformat(
                expiracao.replace("Z", "+00:00")
            )

            if data_expiracao.tzinfo is None:
                data_expiracao = data_expiracao.replace(tzinfo=timezone.utc)

            if data_expiracao > datetime.now(timezone.utc) + timedelta(minutes=5):
                return tokens["access_token"]

        if not MELI_CLIENT_ID or not MELI_CLIENT_SECRET:
            raise RuntimeError(
                "Configure MELI_CLIENT_ID e MELI_CLIENT_SECRET no Render."
            )

        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise RuntimeError("Refresh token ausente no Supabase.")

        resposta = requests.post(
            TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "grant_type": "refresh_token",
                "client_id": MELI_CLIENT_ID,
                "client_secret": MELI_CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
            timeout=TIMEOUT,
        )

        if not resposta.ok:
            logging.error(
                "Falha ao renovar token Mercado Livre: HTTP %s",
                resposta.status_code,
            )
            resposta.raise_for_status()

        dados = resposta.json()
        _salvar_tokens(dados)

        logging.info("Tokens do Mercado Livre renovados e salvos.")
        return dados["access_token"]


def buscar_produtos(termo, limite=10):
    """
    Busca produtos no Mercado Livre Brasil.
    Não gera links de afiliado e não publica no Telegram.
    """
    termo = str(termo or "").strip()
    if not termo:
        return []

    token = obter_access_token()

    resposta = requests.get(
        SEARCH_URL,
        params={
            "q": termo,
            "limit": max(1, min(int(limite), 50)),
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT,
    )
    if not resposta.ok:
    logging.error(
        "Erro na busca Mercado Livre: HTTP %s | Resposta: %s",
        resposta.status_code,
        resposta.text[:1000]
    )
    resposta.raise_for_status()

    dados = resposta.json()
    produtos = []

    for item in dados.get("results", []):
        preco = item.get("price")
        if preco is None or float(preco) <= 0:
            continue

        preco_original = item.get("original_price")
        desconto = None

        if preco_original and float(preco_original) > float(preco):
            desconto = round(
                (1 - float(preco) / float(preco_original)) * 100,
                1,
            )

        produtos.append({
            "id": str(item.get("id") or ""),
            "titulo": item.get("title") or "",
            "preco_min": float(preco),
            "preco_max": float(preco),
            "preco_original": (
                float(preco_original) if preco_original else None
            ),
            "desconto_anunciado": desconto,
            "vendas": item.get("sold_quantity", 0),
            "avaliacao": None,
            "loja": item.get("seller", {}).get("nickname", ""),
            "foto": item.get("thumbnail") or "",
            "link_produto": item.get("permalink") or "",
            "link_afiliado": "",
            "origem": "mercadolivre",
        })

    return produtos
