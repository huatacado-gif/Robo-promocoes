# shopee_api.py
import os
import json
import time
import hashlib
import logging
import requests

API_URL = "https://open-api.affiliate.shopee.com.br/graphql"

APP_ID = os.environ.get("SHOPEE_APP_ID", "").strip()
SECRET = os.environ.get("SHOPEE_SECRET", "").strip()


def consultar_api(query):
    """Executa uma consulta GraphQL assinada. Nunca imprime o segredo."""
    if not APP_ID or not SECRET:
        raise RuntimeError(
            "Configure SHOPEE_APP_ID e SHOPEE_SECRET no ambiente."
        )

    body = {"query": query}
    payload = json.dumps(
        body, ensure_ascii=False, separators=(",", ":")
    )
    timestamp = str(int(time.time()))

    # Assinatura usada pelo protocolo da API.
    base = APP_ID + timestamp + payload + SECRET
    assinatura = hashlib.sha256(base.encode("utf-8")).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "Authorization": (
            f"SHA256 Credential={APP_ID}, Timestamp={timestamp}, "
            f"Signature={assinatura}"
        ),
    }

    response = requests.post(
        API_URL,
        data=payload.encode("utf-8"),
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()

    resultado = response.json()

    if resultado.get("errors"):
        logging.error(
            "A API Shopee retornou erros: %s",
            json.dumps(resultado["errors"], ensure_ascii=False),
        )
        return None

    return resultado.get("data")


def buscar_ofertas(termo, pagina=1, limite=20):
    """Busca produtos individuais. A consulta depende do schema habilitado."""
    termo_seguro = json.dumps(termo, ensure_ascii=False)

    query = f"""
    {{
      productOfferV2(
        keyword: {termo_seguro},
        page: {int(pagina)},
        limit: {max(1, min(int(limite), 50))}
      ) {{
        nodes {{
          itemId
          productName
          productLink
          offerLink
          imageUrl
          priceMin
          priceMax
          priceDiscountRate
          sales
          ratingStar
          commissionRate
          shopName
        }}
        pageInfo {{
          page
          limit
          hasNextPage
        }}
      }}
    }}
    """

    dados = consultar_api(query)
    if not dados:
        return []

    bloco = dados.get("productOfferV2") or {}
    produtos = bloco.get("nodes") or []

    # Só retornamos os campos que usaremos no filtro.
    return [
        {
            "id": str(p.get("itemId", "")),
            "titulo": p.get("productName", ""),
            "preco_min": p.get("priceMin"),
            "preco_max": p.get("priceMax"),
            "desconto_anunciado": p.get("priceDiscountRate"),
            "vendas": p.get("sales"),
            "avaliacao": p.get("ratingStar"),
            "comissao": p.get("commissionRate"),
            "loja": p.get("shopName", ""),
            "foto": p.get("imageUrl", ""),
            "link_produto": p.get("productLink", ""),
            "link_afiliado": p.get("offerLink", ""),
        }
        for p in produtos
    ]


if __name__ == "__main__":
    for termo in [
        "organizador de cozinha",
        "parafusadeira",
        "fone bluetooth",
    ]:
        try:
            itens = buscar_ofertas(termo)
            print(f"\nBusca: {termo} | Encontrados: {len(itens)}")

            for item in itens[:5]:
                print(
                    item["titulo"],
                    "| preço:", item["preco_min"],
                    "| desconto informado:", item["desconto_anunciado"],
                    "| link de afiliado presente:",
                    bool(item["link_afiliado"]),
                )
        except Exception as erro:
            logging.error(
                "Falha ao consultar '%s': %s", termo, erro
            )
