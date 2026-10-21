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
    """Envia uma foto com legenda formatada para o Telegram"""
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
            print(f"Erro ao enviar foto para o Telegram: {response.text}")
    except Exception as e:
        print(f"Erro de conexão: {e}")

# Servidor Web para manter o Render "Live"
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Robo Mercado Livre Pro Ativo!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

def carregar_catalogo_achadinhos():
    """
    CATÁLOGO DE PRODUTOS DO MERCADO LIVRE:
    Seus links de afiliado já estão integrados nos produtos abaixo.
    """
    return [
        {
            "titulo": "Achadinho em Destaque — Mercado Livre",
            "categoria": "Ofertas e Achados",
            "desconto": "Frete Grátis + Condição Especial",
            # Nota: Lembre-se de colocar o link direto da imagem oficial do produto no campo abaixo
            "foto": "https://http2.mlstatic.com/D_NQ_NP_2X_752405-MLB70138942296_062023-F.webp", 
            "link": "https://meli.la/1azkTqG"
        },
        {
            "titulo": "Super Oferta do Dia — Mercado Livre",
            "categoria": "Tendências e Utilidades",
            "desconto": "Melhor preço da semana",
            # Nota: Lembre-se de colocar o link direto da imagem oficial do produto no campo abaixo
            "foto": "https://http2.mlstatic.com/D_NQ_NP_2X_985552-MLB72983758242_112023-F.webp", 
            "link": "https://meli.la/2K1GV2W"
        }
    ]

def rodar_robo():
    enviar_telegram_com_foto(
        "https://http2.mlstatic.com/D_NQ_NP_2X_752405-MLB70138942296_062023-F.webp", 
        "🤖 *Robô Pro Mercado Livre Iniciado!* Postagens fracionadas ativadas."
    )
    
    catalogo = carregar_catalogo_achadinhos()
    
    while True:
        random.shuffle(catalogo)
        print("Iniciando ciclo de postagens fracionadas...")
        
        for item in catalogo:
            mensagem = (
                f"🔥 *ACHADINHO EM DESTAQUE* 🔥\n\n"
                f"📦 *Produto:* {item['titulo']}\n"
                f"📂 *Categoria:* _{item['categoria']}_\n"
                f"💰 *Condição:* {item['desconto']}\n\n"
                f"👉 [Clique aqui para garantir o seu]({item['link']})\n\n"
                f"⚡ _Corre que o estoque é limitado!_"
            )
            
            enviar_telegram_com_foto(item['foto'], mensagem)
            print(f"Produto enviado com sucesso: {item['titulo']}")
            
            # Pausa de 4 horas entre cada postagem (cerca de 5 vezes por dia)
            tempo_de_espera = 14400  
            time.sleep(tempo_de_espera)
            
        print("Ciclo completo. Reiniciando a rotação dos produtos...")

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    rodar_robo()
