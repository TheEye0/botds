# main.py

# -*- coding: utf-8 -*-
"""
BotDS Discord Bot
  - Gera diariamente (às 09:00) uma palavra + frase estoica inéditas
  - Guarda todo histórico em historico.json
  - Nunca repete nenhuma palavra ou frase já publicada
"""

import os
import json
import datetime
import traceback
from pathlib import Path

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from groq import Groq

# Carrega .env
load_dotenv()
DISCORD_TOKEN      = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY")
CANAL_DESTINO_ID   = int(os.getenv("CANAL_DESTINO_ID", "0"))
LLAMA_MODEL        = os.getenv("LLAMA_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

# Paths
ROOT_DIR   = Path(__file__).parent
HIST_FILE  = ROOT_DIR / "historico.json"

# Inicia cliente Groq
groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)

# -------------------------------------------------------
# FUNÇÕES DE HISTÓRICO
# -------------------------------------------------------
def carregar_historico():
    if HIST_FILE.exists():
        try:
            return json.loads(HIST_FILE.read_text(encoding="utf-8"))
        except Exception:
            traceback.print_exc()
    # se não existir ou falhar
    return {"palavras": [], "frases": []}

def salvar_historico(hist):
    try:
        HIST_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        traceback.print_exc()

# -------------------------------------------------------
# GERAÇÃO DE CONTEÚDO
# -------------------------------------------------------
async def gerar_conteudo_com_ia():
    if not groq:
        return "❌ Serviço de geração indisponível."

    hist = carregar_historico()
    all_palavras = {p.lower() for p in hist["palavras"]}
    all_frases   = {f.lower() for f in hist["frases"]}

    prompt = """
Crie uma palavra em inglês com definição, exemplo em inglês e tradução para o português.

Em seguida, forneça uma frase estoica em português com sua explicação em português.

Use EXATAMENTE este formato, cada item em nova linha:

Palavra: <palavra>
Definição: <definição em português>
Exemplo: <exemplo em inglês>
Tradução do exemplo: <tradução>
Frase estoica: <frase em português>
Explicação: <explicação em português>
"""

    for tentativa in range(1, 16):
        try:
            resp = groq.chat.completions.create(
                model=LLAMA_MODEL,
                messages=[
                    {"role": "system", "content": "Você é um professor de inglês e filosofia estoica."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7
            )
            content = resp.choices[0].message.content.strip()
        except Exception as e:
            traceback.print_exc()
            return f"❌ Erro na geração de conteúdo: {e}"

        # extrair palavra e frase
        palavra = None
        frase   = None
        for line in content.splitlines():
            if line.startswith("Palavra:"):
                palavra = line.split("Palavra:",1)[1].strip()
            if line.startswith("Frase estoica:"):
                frase = line.split("Frase estoica:",1)[1].strip()

        if not palavra or not frase:
            continue  # retry

        if palavra.lower() in all_palavras or frase.lower() in all_frases:
            # já existe
            continue

        # inédito: grava no histórico e retorna
        hist["palavras"].append(palavra)
        hist["frases"].append(frase)
        salvar_historico(hist)
        return content

    return "⚠️ Não consegui gerar conteúdo inédito após várias tentativas."

# -------------------------------------------------------
# AGENDAMENTO DIÁRIO
# -------------------------------------------------------
@tasks.loop(time=datetime.time(hour=9, minute=0))
async def enviar_conteudo_diario():
    if CANAL_DESTINO_ID == 0:
        print("⚠️ CANAL_DESTINO_ID não configurado; pulando envio diário.")
        return

    canal = bot.get_channel(CANAL_DESTINO_ID)
    if canal is None:
        print(f"⚠️ Canal {CANAL_DESTINO_ID} não encontrado.")
        return

    try:
        texto = await gerar_conteudo_com_ia()
        # caso seja muito longo, quebra em blocos
        for i in range(0, len(texto), 2000):
            await canal.send(texto[i:i+2000])
        print(f"✅ Conteúdo diário enviado em {datetime.datetime.now()}.")
    except Exception:
        traceback.print_exc()

@enviar_conteudo_diario.before_loop
async def before_daily():
    await bot.wait_until_ready()
    print("🕘 Bot pronto: começando o loop de envio diário às 09:00.")

# -------------------------------------------------------
# STARTUP
# -------------------------------------------------------
@bot.event
async def on_ready():
    print(f"🤖 Bot online: {bot.user}")
    if not enviar_conteudo_diario.is_running():
        enviar_conteudo_diario.start()

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ ERRO: DISCORD_TOKEN não definido.")
    else:
        bot.run(DISCORD_TOKEN)
