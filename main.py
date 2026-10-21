import os
import time
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

# Configurações do Telegram
TELEGRAM_TOKEN = "8785541549:AAHjKxGNC6kSBJ5VZRCcxlOSuHe6uwAGOrU"
TELEGRAM_CHAT_ID = "723715057"

# ==============================================================================
# O SEU LINK DE AFILIADO PRINCIPAL DA SHOPEE
# (https://collshp.com/acadinhoseofertass?view=storefront)
# ==============================================================================
LINK_AFILIADO_SHOPEE = "https://shopee.com.br/m/ofertas-relampago"

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
        self.wfile.write(b"Robo Shopee Liquidation Ativo!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

def obter_campanhas_liquidacao():
    """
    Retorna campanhas automáticas de liquidação e achadinhos da Shopee
    integradas com o seu link de afiliado.
    """
    campanhas = [
        {
            "titulo": "⚡ Ofertas Relâmpago do Dia (Até 60% OFF)",
            "categoria": "Eletrónicos, Casa e Utilidades",
            "detalhe": "Produtos selecionados com super descontos por tempo limitado.",
            "link": LINK_AFILIADO_SHOPEE
        },
        {
            "titulo": "📦 Achadinhos Virais e Frete Grátis",
            "categoria": "Achadinhos da Internet",
            "detalhe": "Os itens mais desejados e vendidos da semana na Shopee.",
            "link": LINK_AFILIADO_SHOPEE
        },
        {
            "titulo": "🔥 Liquidação de Eletrónicos e Acessórios",
            "categoria": "Tecnologia e Gadgets",
            "detalhe": "Fones, carregadores, suportes e muito mais em promoção.",
            "link": LINK_AFILIADO_SHOPEE
        }
    ]
    return campanhas

def rodar_robo():
    enviar_telegram("🟡 *Robô de Liquidação Automática Shopee Ativo!* Monitorizando ofertas...")
    
    while True:
        print("Buscando campanhas de liquidação na Shopee...")
        ofertas = obter_campanhas_liquidacao()
        
        for item in ofertas:
            mensagem = (
                f"🔥 *{item['titulo']}* 🔥\n\n"
                f"📂 *Categoria:* _{item['categoria']}_\n"
                f"📝 *Detalhe:* {item['detalhe']}\n\n"
                f"👉 [Aproveitar Descontos Agora]({item['link']})"
            )
            enviar_telegram(mensagem)
            time.sleep(5)  # Intervalo de 5 segundos entre o envio de cada campanha
            
        print("Aguardando 1 hora para o próximo ciclo de campanhas...")
        time.sleep(3600)  # Repete o ciclo de campanhas a cada 1 hora

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    rodar_robo()
