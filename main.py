import os
import time
import requests

def buscar_ofertas():
    print("Buscando ofertas do dia...")
    ofertas = [
        {"titulo": "Fone Bluetooth Sem Fio", "preco": "R$ 59,90", "link": "https://shopee.com.br"},
        {"titulo": "Smartwatch Relógio Inteligente", "preco": "R$ 120,00", "link": "https://mercadolivre.com.br"}
    ]
    return ofertas

def rodar():
    print("Iniciando varredura...")
    produtos = buscar_ofertas()
    for item in produtos:
        mensagem = f"🔥 *OFERTA IMPERDÍVEL!* 🔥\n\n📦 {item['titulo']}\n💰 Por apenas: {item['preco']}\n👉 Confira aqui: {item['link']}"
        print("Mensagem gerada:\n", mensagem)
        print("-" * 30)

if __name__ == "__main__":
    while True:
        rodar()
        print("Aguardando 10 minutos para a próxima verificação...")
        time.sleep(600)  # Aguarda 600 segundos (10 minutos)
