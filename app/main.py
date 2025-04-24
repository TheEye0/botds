# -*- coding: utf-8 -*-
"""
main.py — BotDS Discord Bot
Integra Groq + SerpApi, persiste histórico local em historico.json e faz upload via github_uploader.
Certifique-se de que upload_to_github(path) aceite o caminho absoluto.
"""
import os
import json
import datetime
import traceback
import re
from collections import defaultdict, deque
from threading import Thread

import discord
from discord.ext import commands, tasks
from flask import Flask
from dotenv import load_dotenv
from groq import Groq
from serpapi import GoogleSearch

# Importa uploader do GitHub (app/github_uploader.py)
# Agora exporta upload_to_github(path: str)
from app.github_uploader import upload_to_github

# --- Environment ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY  = os.getenv("GROQ_API_KEY")
SERPAPI_KEY   = os.getenv("SERPAPI_KEY")
ALLOWED_GUILD = int(os.getenv("ALLOWED_GUILD_ID", "0"))
ALLOWED_USER  = int(os.getenv("ALLOWED_USER_ID", "0"))
DEST_CHANNEL  = int(os.getenv("CANAL_DESTINO_ID", "0"))
LLAMA_MODEL   = os.getenv("LLAMA_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

# Caminho local do histórico (usa caminho absoluto para garantir consistência)
HISTORICO_FILE_PATH = os.getenv("HISTORICO_FILE_PATH", "historico.json")
HISTORY_FILE = os.path.join(os.path.dirname(__file__), HISTORICO_FILE_PATH)

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
# Mantém até 10 mensagens de contexto por canal (apenas mensagens do usuário)
conversas = defaultdict(lambda: deque(maxlen=10))

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# --- Helpers ---
def autorizado(ctx):
    return (
        (isinstance(ctx.channel, discord.DMChannel) and ctx.author.id == ALLOWED_USER) or
        (ctx.guild and ctx.guild.id == ALLOWED_GUILD)
    )

# --- Histórico local e upload via GitHub ---
def carregar_historico():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"palavras": [], "frases": []}
    except Exception:
        traceback.print_exc()
        return {"palavras": [], "frases": []}


def salvar_historico(hist: dict):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
    except Exception:
        traceback.print_exc()
    # Envia atualização para o GitHub, passando path absoluto
    upload_to_github(HISTORY_FILE)

# --- Geração de conteúdo diário ---
async def gerar_conteudo_com_ia() -> str:
    if not groq_client:
        return "⚠️ Serviço de geração indisponível."

    hist = carregar_historico()
    prompt = (
        "Crie uma palavra em inglês (definição em português, exemplo em inglês e tradução).\n"
        "Depois, forneça uma frase estoica em português com explicação.\n"
        "Use este formato exato (uma linha por item):\n"
        "Palavra: <palavra>\n"
        "Definição: <definição em português>\n"
        "Exemplo: <exemplo em inglês>\n"
        "Tradução do exemplo: <tradução em português>\n"
        "Frase estoica: <frase em português>\n"
        "Explicação: <explicação em português>"
    )
    raw = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[
            {"role": "system", "content": "Você é um professor de inglês e filosofia estoica."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    ).choices[0].message.content.strip()

    # Extrai apenas o primeiro bloco completo
    match = re.search(r'(?im)^Palavra:.*?Explicação:.*?(?=^Palavra:|\Z)', raw, re.DOTALL)
    resp = match.group(0).strip() if match else raw

    palavra = None
    frase = None
    for line in resp.splitlines():
        l = line.strip()
        if l.lower().startswith("palavra:"):
            palavra = l.split(":", 1)[1].strip()
        elif l.lower().startswith("frase estoica:"):
            frase = l.split(":", 1)[1].strip()

    updated = False
    if palavra and palavra.lower() not in [p.lower() for p in hist.get("palavras", [])]:
        hist.setdefault("palavras", []).append(palavra)
        updated = True
    if frase and frase.lower() not in [f.lower() for f in hist.get("frases", [])]:
        hist.setdefault("frases", []).append(frase)
        updated = True
    if updated:
        salvar_historico(hist)

    return resp

# --- Loop diário ---
@tasks.loop(minutes=1)
async def enviar_conteudo_diario():
    now = datetime.datetime.now()
    if now.hour == 9 and now.minute == 0 and DEST_CHANNEL:
        chan = bot.get_channel(DEST_CHANNEL)
        if chan:
            await chan.send(await gerar_conteudo_com_ia())

# --- Eventos ---
@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")
    enviar_conteudo_diario.start()

# --- Comandos ---
@bot.command()
async def ask(ctx, *, pergunta: str):
    if not autorizado(ctx) or not groq_client:
        return await ctx.send("❌ Não autorizado ou serviço indisponível.")

    # Guarda apenas o histórico de perguntas (sem respostas) para contexto
    hist_user = conversas[ctx.channel.id]
    hist_user.append(pergunta)

    # Constrói prompt com histórico de perguntas
    messages = [{"role": "system", "content": "Você é um assistente prestativo."}]
    for q in hist_user:
        messages.append({"role": "user", "content": q})

    # Chama a API
    resp = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=messages,
        temperature=0.7
    ).choices[0].message.content

    # Limpa histórico mantendo apenas últimas perguntas
    if len(hist_user) > 9:
        hist_user.popleft()

    await ctx.send(resp)

@bot.command()
async def search(ctx, *, consulta: str):
    if not autorizado(ctx) or not SERPAPI_KEY:
        return await ctx.send("❌ Não autorizado ou SERPAPI_KEY ausente.")
    await ctx.send(f"🔍 Buscando: {consulta}")
    # Busca resultados com SerpApi e obtém os primeiros 3
    results = (
        GoogleSearch({"q": consulta, "hl": "pt-br", "gl": "br", "api_key": SERPAPI_KEY})
        .get_dict()
        .get("organic_results", [])[:3]
    )
    snippet = (
        "\n\n"
    ).join(
        f"**{r['title']}**: {r['snippet']}"
        for r in results
    ) or "Nenhum resultado."
    resumo = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[
            {"role": "system", "content": "Resuma resultados."},
            {"role": "user", "content": snippet}
        ],
        temperature=0.3
    ).choices[0].message.content
    await ctx.send(resumo)

@bot.command()
async def testar_conteudo(ctx):
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    await ctx.send(await gerar_conteudo_com_ia())

# --- Keep-alive Flask ---
app = Flask(__name__)
@app.route("/")
def home():
    return f"Bot {bot.user.name if bot.user else ''} online!"


def run_server():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)), use_reloader=False)

# --- Main ---
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERRO: DISCORD_TOKEN não definido.")
    else:
        Thread(target=run_server, daemon=True).start()
        bot.run(DISCORD_TOKEN)
