import os
import time
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from shopee_api import buscar_ofertas

# Configuração de logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Servidor Web simples para manter o Render 'Live'
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot de Promocoes Ativo!")

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    logging.info(f"Servidor Web iniciado na porta {port}")
    server.serve_forever()

# Função de Teste da Shopee
def testar_busca_shopee():
    logging.info("--- INICIANDO TESTE DA API SHOPEE ---")
    buscas = {
        "utilidades": [
            "organizador de cozinha",
            "air fryer",
        ],
        "ferramentas": [
            "parafusadeira",
            "furadeira",
        ],
        "eletronicos": [
            "fone bluetooth",
            "smartwatch",
        ],
    }

    for categoria, termos in buscas.items():
        for termo in termos:
            try:
                produtos = buscar_ofertas(termo)

                logging.info(
                    "Shopee | categoria=%s | termo=%s | encontrados=%d",
                    categoria,
                    termo,
                    len(produtos),
                )

                for produto in produtos[:3]:
                    logging.info(
                        "Produto=%s | preco=%s | desconto=%s | link_afiliado=%s",
                        produto.get("titulo"),
                        produto.get("preco_min"),
                        produto.get("desconto_anunciado"),
                        bool(produto.get("link_afiliado")),
                    )

            except Exception:
                logging.exception(
                    "Falha na busca Shopee para o termo: %s",
                    termo,
                )
    logging.info("--- TESTE DA API SHOPEE CONCLUÍDO ---")

if __name__ == "__main__":
    # Inicia o servidor em segundo plano
    threading.Thread(
        target=run_web_server,
        daemon=True,
    ).start()

    # Executa o teste da Shopee uma vez
    testar_busca_shopee()

    # Loop para manter o container rodando
    while True:
        time.sleep(60)
