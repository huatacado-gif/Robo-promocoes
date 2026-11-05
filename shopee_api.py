# shopee_api_atualizado.py
# Substitua o shopee_api.py atual por este arquivo (renomeando para shopee_api.py).

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
    """Executa uma consulta GraphQL assinada sem expor o segredo."""
    if not APP_ID or not SECRET:
        raise RuntimeError("Configure SHOPEE_APP_ID e SHOPEE_SECRET no ambiente.")

    body = {"query": query}
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    timestamp = str(int(time.time()))
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
        API_URL, data=payload.encode("utf-8"), headers=headers, timeout=30
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


def buscar_ofertas(termo, pagina=1, limite=50, max_paginas=2):
    """Busca produtos mais vendidos; paginação limitada para controlar chamadas.

    limite: até 50 produtos por página (limite usado nesta implementação).
    max_paginas: quantidade máxima de páginas consultadas por termo.
    Retorna os mesmos campos que a versão anterior para compatibilidade com main.py.
    """
    termo_seguro = json.dumps(str(termo), ensure_ascii=False)
    limite = max(1, min(int(limite), 50))
    pagina_inicial = max(1, int(pagina))
    max_paginas = max(1, min(int(max_paginas), 10))
    ofertas = []

    for numero_pagina in range(pagina_inicial, pagina_inicial + max_paginas):
        query = f"""
        {{
          productOfferV2(
            keyword: {termo_seguro},
            sortType: 2,
            page: {numero_pagina},
            limit: {limite}
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
            break

        bloco = dados.get("productOfferV2") or {}
        produtos = bloco.get("nodes") or []
        page_info = bloco.get("pageInfo") or {}

        ofertas.extend(
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
        )

        logging.info(
            "Shopee termo=%r página=%s retornou=%s hasNextPage=%s",
            termo, numero_pagina, len(produtos), page_info.get("hasNextPage")
        )

        if not page_info.get("hasNextPage") or not produtos:
            break

    # Remove duplicados pelo ID, preservando a primeira ocorrência.
    unicos = {}
    for produto in ofertas:
        identificador = produto.get("id")
        chave = identificador or produto.get("titulo")
        if chave and chave not in unicos:
            unicos[chave] = produto

    return list(unicos.values())


if __name__ == "__main__":
    for termo in ["organizador de cozinha", "parafusadeira", "fone bluetooth"]:
        try:
            itens = buscar_ofertas(termo)
            print(f"\nBusca: {termo} | Encontrados: {len(itens)}")
            for item in itens[:5]:
                print(
                    item["titulo"], "| preço:", item["preco_min"],
                    "| desconto informado:", item["desconto_anunciado"],
                    "| vendas:", item["vendas"],
                    "| avaliação:", item["avaliacao"],
                    "| link afiliado presente:", bool(item["link_afiliado"]),
                )
        except Exception as erro:
            logging.exception("Falha ao consultar '%s': %s", termo, erro)
