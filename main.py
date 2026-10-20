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
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("Mensagem enviada com sucesso para o Telegram!")
        else:
            print(f"Erro ao enviar para o Telegram: {response.text}")
    except Exception as e:
        print(f"Erro de conexão com o Telegram: {e}")

# Servidor Web simples para manter o Render "Live"
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Robo de Promocoes com Telegram esta rodando!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

def buscar_ofertas():
    # Exemplo de ofertas simuladas (depois vamos integrar com as buscas reais)
    ofertas = [
        {"titulo": "Fone Bluetooth Sem Fio TWS", "preco": "R$ 59,90", "link": "https://shopee.com.br"},
        {"titulo": "Smartwatch Relógio Inteligente D20", "preco": "R$ 49,90", "link": "https://mercadolivre.com.br"}
    ]
    return ofertas

def rodar_robo():
    # Envia uma mensagem inicial avisando que o robô ligou
    enviar_telegram("🤖 *Robô de Promoções iniciado com sucesso!* Monitorando ofertas...")
    
    while True:
        print("Iniciando varredura de ofertas...")
        produtos = buscar_ofertas()
        for item in produtos:
            mensagem = f"🔥 *OFERTA IMPERDÍVEL!* 🔥\n\n📦 *{item['titulo']}*\n💰 Por apenas: *{item['preco']}*\n👉 [Clique aqui para comprar]({item['link']})"
            enviar_telegram(mensagem)
            time.sleep(2) # Pequena pausa entre as mensagens
            
        print("Aguardando 30 minutos para a próxima verificação...")
        time.sleep(1800) # Intervalo de 30 minutos entre as varreduras

if __name__ == "__main__":
    # Inicia o servidor Web em segundo plano para o Render
    threading.Thread(target=run_web_server, daemon=True).start()
    # Inicia o loop principal do robô
    rodar_robo()
