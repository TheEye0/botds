# -*- coding: utf-8 -*-
"""
main.py — BotDS Discord Bot
Integra Groq + SerpApi e persiste histórico via GitHub API.
"""
import os
import json
import traceback
import re
from datetime import time as _time

import base64
import requests
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from groq import Groq
from serpapi import GoogleSearch

# --- Environment ---
load_dotenv()
DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY")
SERPAPI_KEY      = os.getenv("SERPAPI_KEY")
GITHUB_TOKEN     = os.getenv("GITHUB_TOKEN")
GITHUB_REPO      = os.getenv("GITHUB_REPO")
HISTORICO_PATH   = os.getenv("HISTORICO_FILE_PATH", "historico.json")
ALLOWED_GUILD_ID = int(os.getenv("ALLOWED_GUILD_ID", "0"))
ALLOWED_USER_ID  = int(os.getenv("ALLOWED_USER_ID", "0"))
DEST_CHANNEL_ID  = int(os.getenv("CANAL_DESTINO_ID", "0"))
LLAMA_MODEL      = os.getenv("LLAMA_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

# --- Discord Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# --- Helpers ---
def autorizado(ctx):
    return (
        (isinstance(ctx.channel, discord.DMChannel) and ctx.author.id == ALLOWED_USER_ID) or
        (ctx.guild and ctx.guild.id == ALLOWED_GUILD_ID)
    )

# --- GitHub History Persistence ---
GITHUB_API_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

def fetch_history():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_PATH}"
    try:
        resp = requests.get(url, headers=GITHUB_API_HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            content = base64.b64decode(data.get("content", ""))
            hist = json.loads(content)
            return hist, data.get("sha")
    except Exception:
        traceback.print_exc()
    return {"palavras": [], "frases": []}, None


def push_history(hist, sha=None):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_PATH}"
    content_b64 = base64.b64encode(json.dumps(hist, ensure_ascii=False).encode()).decode()
    payload = {"message": "Atualiza histórico pelo bot", "content": content_b64, "branch": "main"}
    if sha:
        payload["sha"] = sha
    try:
        put = requests.put(url, headers=GITHUB_API_HEADERS, json=payload, timeout=10)
        put.raise_for_status()
    except Exception:
        traceback.print_exc()

# --- Prompt and Parsing ---
def build_prompt():
    return (
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

def parse_block(raw):
    m = re.search(r'(?im)^Palavra:.*?Explicação:.*?(?=^Palavra:|\Z)', raw, re.DOTALL)
    return m.group(0).strip() if m else raw.strip()

# --- Generation and History Update ---
async def generate_and_update():
    if not groq_client:
        return "⚠️ Serviço indisponível."
    prompt = build_prompt()
    resp = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[{"role":"system","content":"Você é um professor de inglês e estoico."},
                  {"role":"user","content":prompt}],
        temperature=0.7
    ).choices[0].message.content
    block = parse_block(resp)

    hist, sha = fetch_history()
    # extrai palavra e frase
    palavra = re.search(r'(?im)^Palavra: *(.*)', block)
    frase = re.search(r'(?im)^Frase estoica: *(.*)', block)
    updated = False
    if palavra and palavra.group(1).lower() not in [p.lower() for p in hist["palavras"]]:
        hist["palavras"].append(palavra.group(1).strip())
        updated = True
    if frase and frase.group(1).lower() not in [f.lower() for f in hist["frases"]]:
        hist["frases"].append(frase.group(1).strip())
        updated = True
    if updated:
        push_history(hist, sha)
    return block

# --- Unified Send ---
async def send_content(channel):
    content = await generate_and_update()
    await channel.send(content)

# --- Commands ---
@bot.command()
async def testar_conteudo(ctx):
    """Envia conteúdo agora."""
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    await send_content(ctx.channel)

# --- Scheduled ---
@tasks.loop(time=_time(hour=9, minute=0))
async def daily_send():
    ch = bot.get_channel(DEST_CHANNEL_ID)
    if ch:
        await send_content(ch)

# --- Events ---
@bot.event
async def on_ready():
    print(f"✅ Online: {bot.user} | Guilds: {len(bot.guilds)}")
    daily_send.start()

# --- Main ---
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
