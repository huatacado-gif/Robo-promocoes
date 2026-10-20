import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Servidor Web simples para o Render saber que a aplicação está ativa
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Robo de Promocoes esta rodando!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

def buscar_ofertas():
    print("Buscando ofertas do dia...")
    ofertas = [
        {"titulo": "Fone Bluetooth Sem Fio", "preco": "R$ 59,90", "link": "https://shopee.com.br"},
        {"titulo": "Smartwatch Relógio Inteligente", "preco": "R$ 120,00", "link": "https://mercadolivre.com.br"}
    ]
    return ofertas

def rodar_robo():
    while True:
        print("Iniciando varredura...")
        produtos = buscar_ofertas()
        for item in produtos:
            mensagem = f"🔥 *OFERTA IMPERDÍVEL!* 🔥\n\n📦 {item['titulo']}\n💰 Por apenas: {item['preco']}\n👉 Confira aqui: {item['link']}"
            print("Mensagem gerada:\n", mensagem)
            print("-" * 30)
        print("Aguardando 10 minutos para a próxima verificação...")
        time.sleep(600)

if __name__ == "__main__":
    # Inicia o servidor Web em segundo plano
    threading.Thread(target=run_web_server, daemon=True).start()
    # Inicia o loop do robô
    rodar_robo()
