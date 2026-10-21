
import os
import time
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

# Configurações do Telegram
TELEGRAM_TOKEN = "8785541549:AAHjKxGNC6kSBJ5VZRCcxlOSuHe6uwAGOrU"
TELEGRAM_CHAT_ID = "723715057"

# O seu link principal de afiliado da Shopee
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
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Erro ao enviar: {e}")

class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Robo Pro Mode Ativo!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

def obter_catalogo_pro():
    """
    Catálogo dinâmico simulando achadinhos virais e categorias de alta conversão,
    direcionados automaticamente para o seu link de afiliado.
    """
    return [
        {
            "titulo": "🔥 Fone de Ouvido Bluetooth TWS de Alta Qualidade",
            "categoria": "Eletrónicos e Tecnologia",
            "preco_antigo": "R$ 89,90",
            "preco_novo": "R$ 34,90",
            "desconto": "61% OFF",
            "link": LINK_AFILIADO_SHOPEE
        },
        {
            "titulo": "🏡 Mini Processador de Alimentos USB Recarregável",
            "categoria": "Casa e Cozinha",
            "preco_antigo": "R$ 59,90",
            "preco_novo": "R$ 22,90",
            "desconto": "61% OFF",
            "link": LINK_AFILIADO_SHOPEE
        },
        {
            "titulo": "⚡ Carregador Portátil Power Bank Slim 10000mAh",
            "categoria": "Acessórios para Telemóvel",
            "preco_antigo": "R$ 119,90",
            "preco_novo": "R$ 49,90",
            "desconto": "58% OFF",
            "link": LINK_AFILIADO_SHOPEE
        }
    ]

def rodar_robo():
    enviar_telegram("⭐ *Robô Pro (Modo Achadinhos) Iniciado com Sucesso!*")
    
    while True:
        print("Executando varredura do catálogo Pro...")
        produtos = obter_catalogo_pro()
        
        for item in produtos:
            mensagem = (
                f"🚨 *ACHADINHO EM DESTAQUE* 🚨\n\n"
                f"📦 *{item['titulo']}*\n"
                f"📂 Categoria: _{item['categoria']}_\n\n"
                f"❌ De: ~{item['preco_antigo']}~\n"
                f"✅ Por apenas: *{item['preco_novo']}* ({item['desconto']})\n\n"
                f"🛒 *Garanta o seu com desconto:* [Clique Aqui para Comprar]({item['link']})\n\n"
                f"⚡ *Corre que o estoque é limitado!*"
            )
            enviar_telegram(mensagem)
            time.sleep(4)  # Intervalo de segurança entre envios
            
        print("Ciclo concluído. Aguardando 1 hora para a próxima rodada...")
        time.sleep(3600)

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    rodar_robo()
