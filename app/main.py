# -*- coding: utf-8 -*-
"""
main.py — BotDS Discord Bot
Versão funcional recuperada: integra Groq + SerpApi, persiste histórico via GitHub API,
comandos ask, search, testar_conteudo, e keep-alive HTTP sem lógicas de duplicação avançadas.
"""
import os
import json
import traceback
import re
import base64
import requests
import discord
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict, deque
from datetime import time as _time
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
PORT             = int(os.getenv("PORT", "10000"))

# --- HTTP Keep-alive ---
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot online!")
Thread(target=lambda: HTTPServer(("0.0.0.0", PORT), KeepAliveHandler).serve_forever(), daemon=True).start()

# --- Discord Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
conversas = defaultdict(lambda: deque(maxlen=10))
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# --- Helpers ---
def autorizado(ctx):
    return ((isinstance(ctx.channel, discord.DMChannel) and ctx.author.id == ALLOWED_USER_ID)
            or (ctx.guild and ctx.guild.id == ALLOWED_GUILD_ID))

# --- GitHub Persistence ---
GITHUB_HEADERS = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

def fetch_history():
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_PATH}",
            headers=GITHUB_HEADERS, timeout=10
        )
        if resp.ok:
            data = resp.json()
            raw = base64.b64decode(data.get("content", ""))
            return json.loads(raw), data.get("sha")
    except Exception:
        traceback.print_exc()
    return {"palavras": [], "frases": []}, None


def push_history(hist, sha=None):
    try:
        content_b64 = base64.b64encode(
            json.dumps(hist, ensure_ascii=False).encode()
        ).decode()
        payload = {"message": "Atualiza historico.json pelo bot", "content": content_b64, "branch": "main"}
        if sha:
            payload["sha"] = sha
        put_resp = requests.put(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_PATH}",
            headers=GITHUB_HEADERS, json=payload, timeout=10
        )
        put_resp.raise_for_status()
    except Exception:
        traceback.print_exc()

# --- Content Generation ---
async def gerar_conteudo_com_ia():
    if not groq_client:
        return "⚠️ Serviço indisponível."
    hist, sha = fetch_history()
    prompt = (
        "Crie uma palavra em inglês (definição em português, exemplo em inglês e tradução).\n"
        "Depois, forneça uma frase estoica em português com explicação.\n"
        "Formato: uma linha por item: Palavra:..., Definição:..., Exemplo:..., Tradução:..., Frase estoica:..., Explicação:..."
    )
    resp = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[
            {"role": "system", "content": "Você é um professor de inglês e estoico."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    ).choices[0].message.content.strip()
    block = resp
    # extrai palavra/frase
    palavra = re.search(r'(?im)^Palavra: *(.+)$', block)
    frase = re.search(r'(?im)^Frase estoica: *(.+)$', block)
    updated = False
    if palavra:
        p = palavra.group(1).strip()
        if p.lower() not in [x.lower() for x in hist.get("palavras", [])]:
            hist.setdefault("palavras", []).append(p)
            updated = True
    if frase:
        f = frase.group(1).strip()
        if f.lower() not in [x.lower() for x in hist.get("frases", [])]:
            hist.setdefault("frases", []).append(f)
            updated = True
    if updated:
        push_history(hist, sha)
    return block

async def send_content(channel):
    await channel.send(await gerar_conteudo_com_ia())

# --- Commands ---
@bot.command()
async def ask(ctx, *, pergunta: str):
    if not autorizado(ctx) or not groq_client:
        return await ctx.send("❌ Não autorizado ou serviço indisponível.")
    h = conversas[ctx.channel.id]
    h.append({"role": "user", "content": pergunta})
    resp = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[{"role": "system", "content": "Você é um assistente prestativo."}] + list(h),
        temperature=0.7
    ).choices[0].message.content
    h.append({"role": "assistant", "content": resp})
    await ctx.send(resp)

@bot.command()
async def search(ctx, *, consulta: str):
    if not autorizado(ctx) or not SERPAPI_KEY:
        return await ctx.send("❌ Não autorizado ou SERPAPI_KEY ausente.")
    await ctx.send(f"🔍 Buscando: {consulta}")
    results = GoogleSearch({"q": consulta, "hl": "pt-br", "gl": "br", "api_key": SERPAPI_KEY}).get_dict().get("organic_results", [])[:3]
    snippet = "\n".join(f"**{r['title']}**: {r['snippet']}" for r in results) or "Nenhum resultado."
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
    await send_content(ctx.channel)

# --- Scheduled ---
@tasks.loop(time=_time(hour=9, minute=0))
async def daily_send():
    ch = bot.get_channel(DEST_CHANNEL_ID)
    if ch:
        await send_content(ch)

@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")
    daily_send.start()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
