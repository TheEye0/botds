# -*- coding: utf-8 -*-
"""
main.py — BotDS Discord Bot
Integra Groq + SerpApi, persiste histórico via GitHub API, com comandos ask, search, testar_conteudo e keep-alive HTTP.
Implementa detecção de duplicação via leitura do histórico de canal e logs de envio de histórico.
"""
import os
import json
import traceback
import re
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict, deque
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
PORT             = int(os.getenv("PORT", "10000"))

# --- Keep-alive HTTP Server ---
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
    return (
        (isinstance(ctx.channel, discord.DMChannel) and ctx.author.id == ALLOWED_USER_ID) or
        (ctx.guild and ctx.guild.id == ALLOWED_GUILD_ID)
    )

async def safe_send(channel, content):
    """Envia content somente se for diferente da última mensagem do bot no canal."""
    last = None
    async for msg in channel.history(limit=1):
        last = msg
        break
    if last and last.author.id == bot.user.id and last.content == content:
        return
    await channel.send(content)

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
            return json.loads(content), data.get("sha")
    except Exception:
        traceback.print_exc()
    return {"palavras": [], "frases": []}, None


def push_history(hist, sha=None):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_PATH}"
    content_b64 = base64.b64encode(json.dumps(hist, ensure_ascii=False, indent=2).encode()).decode()
    payload = {"message": "Atualiza histórico pelo bot", "content": content_b64, "branch": "main"}
    if sha:
        payload["sha"] = sha
    try:
        r = requests.put(url, headers=GITHUB_API_HEADERS, json=payload, timeout=10)
        print(f"[HIST PUT] status={r.status_code} text={r.text}")
        r.raise_for_status()
    except Exception:
        traceback.print_exc()

# --- Prompt and Parsing ---
def build_prompt(used_palavras, used_frases):
    hist_text = ""
    if used_palavras:
        hist_text += "Palavras já usadas: " + ", ".join(used_palavras) + ".\n"
    if used_frases:
        hist_text += "Frases já usadas: " + ", ".join(used_frases) + ".\n"
    hist_text += (
        "Com base no histórico acima, gere APENAS uma nova palavra em inglês e uma nova frase estoica em português, "
        "sem repetir nenhuma das já usadas; as palavras não precisam ser da área do estoicismo, podem ser qualquer palavra.\n"
        "Use este formato (uma linha por item, dando espaço entre elas e colocando o campo de cada uma em negrito e a resposta em texto normal):\n"
        "**Palavra**: <palavra>\n"
        "**Definição**: <definição em português>\n"
        "**Exemplo**: <exemplo em inglês>\n"
        "**Tradução do exemplo**: <tradução em português>\n"
        "**Frase estoica**: <frase em português>\n"
        "**Explicação**: <explicação em português>"
    )
    return hist_text


def parse_block(raw):
    m = re.search(r'(?im)^Palavra:.*?Explicação:.*?(?=^Palavra:|\Z)', raw, re.DOTALL)
    return m.group(0).strip() if m else raw.strip()

# --- Generation and History Update ---
async def generate_and_update():
    if not groq_client:
        return "⚠️ Serviço indisponível."
    hist, sha = fetch_history()
    prompt = build_prompt(hist.get("palavras", []), hist.get("frases", []))
    resp = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[
            {"role":"system","content":"Você é um professor de inglês e estoico."},
            {"role":"user","content": prompt}
        ],
        temperature=0.7
    ).choices[0].message.content
    block = parse_block(resp)
    pal = re.search(r'(?im)^Palavra: *(.*)', block)
    fra = re.search(r'(?im)^Frase estoica: *(.*)', block)
    if pal:
        p = pal.group(1).strip()
        if p.lower() not in [x.lower() for x in hist["palavras"]]:
            hist["palavras"].append(p)
    if fra:
        f = fra.group(1).strip()
        if f.lower() not in [x.lower() for x in hist["frases"]]:
            hist["frases"].append(f)
    push_history(hist, sha)
    return block

# --- Helper for chunking messages ---
def chunk_text(text: str, limit: int = 1900):
    return [text[i:i+limit] for i in range(0, len(text), limit)]

# --- Commands ---
@bot.command()
async def ask(ctx, *, pergunta: str):
    if not autorizado(ctx) or not groq_client:
        return await ctx.send("❌ Não autorizado ou serviço indisponível.")
    hist_chan = conversas[ctx.channel.id]
    hist_chan.append({"role": "user", "content": pergunta})
    msgs = [{"role": "system", "content": "Você é um assistente prestativo."}] + list(hist_chan)
    resp = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=msgs,
        temperature=0.7
    ).choices[0].message.content
    hist_chan.append({"role": "assistant", "content": resp})
    for chunk in chunk_text(resp):
        await safe_send(ctx.channel, chunk)

@bot.command()
async def search(ctx, *, consulta: str):
    if not autorizado(ctx) or not SERPAPI_KEY:
        return await ctx.send("❌ Não autorizado ou SERPAPI_KEY ausente.")
    await ctx.send(f"🔍 Buscando: {consulta}")
    results = GoogleSearch({"q": consulta, "hl": "pt-br", "gl": "br", "api_key": SERPAPI_KEY}).get_dict().get("organic_results", [])[:3]
    snippet = "\n\n".join(f"**{r['title']}**: {r['snippet']}" for r in results) or "Nenhum resultado."
    resumo = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[{"role": "system", "content": "Resuma resultados."}, {"role": "user", "content": snippet}],
        temperature=0.3
    ).choices[0].message.content
    for chunk in chunk_text(resumo):
        await safe_send(ctx.channel, chunk)

@bot.command()
async def testar_conteudo(ctx):
    """Envia conteúdo gerado imediatamente."""
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
    import os, threading
    print(f"[READY] PID={os.getpid()} TID={threading.get_ident()} — Bot online: {bot.user} | Guilds: {len(bot.guilds)}")
    daily_send.start()

# --- Main ---
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
