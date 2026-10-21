import os
import time
import threading
import requests
import random
from http.server import HTTPServer, BaseHTTPRequestHandler

# Configurações do Telegram
TELEGRAM_TOKEN = "8785541549:AAGOatZvWW3yLcnmgQmYJEaBtQmv4wOmqHQ"
TELEGRAM_CHAT_ID = "723715057"

def enviar_telegram_com_foto(foto_url, mensagem):
    """Envia UMA ÚNICA mensagem contendo a foto e a legenda com o link"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": foto_url,
        "caption": mensagem,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"Erro ao enviar para o Telegram: {response.text}")
    except Exception as e:
        print(f"Erro de conexão: {e}")

# Servidor Web para manter o Render ligado (24/7)
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Robo Pro Mercado Livre Rodando!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

def carregar_catalogo_achadinhos():
    """
    SEU CATÁLOGO DE PRODUTOS:
    Apenas 1 foto e 1 legenda com link para cada item.
    """
    return [
        {
            "titulo": "Luminária de Mesa / Achadinho",
            "categoria": "Casa e Decoração",
            "desconto": "Frete Grátis + Oferta Especial",
            "foto": "https://http2.mlstatic.com/D_NQ_NP_723177-MLA99366040854_112025-O.webp", 
            "link": "https://meli.la/1qSMXTH"
        },
        {
            "titulo": "Varal de Chão Multiuso Dobrável",
            "categoria": "Utilidades Domésticas",
            "desconto": "Melhor preço da semana",
            "foto": "https://http2.mlstatic.com/D_Q_NP_970780-MLA113098483707_062026-F.webp", 
            "link": "https://meli.la/32wcHqJ"
        },
        {
            "titulo": "Quadro Robo Termoplástico C/ 6 Tomadas Ravesi",
            "categoria": "Ferramentas e Elétrica",
            "desconto": "Apenas R$ 282,90",
            "foto": "https://http2.mlstatic.com/D_Q_NP_772752-MLB114529867748_082026-F-quadro-robo-termoplastico-c-6-tomadas--standard--ravesi.webp", 
            "link": "https://meli.la/2HEQCaD"
        }
    ]

def rodar_robo():
    catalogo = carregar_catalogo_achadinhos()
    
    while True:
        # Embaralha para alternar a ordem dos produtos a cada ciclo
        random.shuffle(catalogo)
        print("Iniciando ciclo de postagens...")
        
        for item in catalogo:
            # Texto da legenda bem espaçado e fácil de ler
            mensagem = (
                f"🔥 *ACHADINHO EM DESTAQUE* 🔥\n\n"
                f"📦 *Produto:* {item['titulo']}\n"
                f"📂 *Categoria:* _{item['categoria']}_\n"
                f"💰 *Condição:* {item['desconto']}\n\n"
                f"👉 [Clique aqui para garantir o seu]({item['link']})\n\n"
                f"⚡ _Corre que o estoque é limitado!_"
            )
            
            # Dispara UMA única mensagem (Foto + Legenda)
            enviar_telegram_com_foto(item['foto'], mensagem)
            print(f"Produto enviado: {item['titulo']}")
            
            # Tempo de espera entre os posts (14400 segundos = 4 horas)
            tempo_de_espera = 14400  
            time.sleep(tempo_de_espera)

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    rodar_robo()
