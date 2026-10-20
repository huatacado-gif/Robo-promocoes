import os
import time
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

# Configurações do Telegram
TELEGRAM_TOKEN = "8785541549:AAHjKxGNC6kSBJ5VZRCcxlOSuHe6uwAGOrU"
TELEGRAM_CHAT_ID = "723715057"

def enviar_telegram(mensagem):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensagem,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"Erro ao enviar para o Telegram: {response.text}")
    except Exception as e:
        print(f"Erro de conexão: {e}")

# Servidor Web para manter o Render "Live"
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Robo Shopee Automatico Ativo!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

def buscar_ofertas_shopee():
    ofertas_automaticas = [
        {
            "titulo": "Achadinhos da Shopee: Ofertas Relâmpago do Dia",
            "categoria": "Eletrónicos e Acessórios",
            "desconto": "Até 60% OFF + Frete Grátis",
            "link": "https://shopee.com.br/m/ofertas-relampago"
        },
        {
            "titulo": "Achadinhos virais de Utilidades para Casa",
            "categoria": "Casa e Cozinha",
            "desconto": "Promoção Especial",
            "link": "https://shopee.com.br/m/achadinhos-da-shopee"
        }
    ]
    return ofertas_automaticas

def rodar_robo():
    enviar_telegram("🟡 *Robô Shopee Automático Iniciado!* Varrendo ofertas e achadinhos...")
    
    while True:
        print("Buscando novas campanhas e ofertas na Shopee...")
        produtos = buscar_ofertas_shopee()
        
        for item in produtos:
            mensagem = (
                "🔥 *ACHADINHO AUTOMÁTICO — SHOPEE*\n\n"
                f"📦 *Produto:* {item['titulo']}\n"
                f"📂 *Categoria:* _{item['categoria']}_\n"
                f"💰 *Condição:* {item['desconto']}\n\n"
                f"👉 [Clique aqui para ver as ofertas]({item['link']})"
            )
            enviar_telegram(mensagem)
            time.sleep(3)
            
        print("Aguardando 1 hora para a próxima varredura...")
        time.sleep(3600)

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    rodar_robo()
