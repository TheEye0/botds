# -*- coding: utf-8 -*-
"""
main.py — BotDS Discord Bot
Integra Groq + SerpApi, persiste histórico via GitHub API,
comandos ask, search, testar_conteudo e keep-alive HTTP.
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
        (isinstance(ctx.channel, discord.DMChannel) and ctx.author.id == ALLOWED_USER_ID)
        or (ctx.guild and ctx.guild.id == ALLOWED_GUILD_ID)
    )

async def safe_send(channel, content):
    last = None
    async for msg in channel.history(limit=1):
        last = msg
        break
    if last and last.author.id == bot.user.id and last.content == content:
        return
    await channel.send(content)

# --- GitHub History Persistence ---
GITHUB_HEADERS = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

def fetch_history():
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_PATH}"
        r = requests.get(url, headers=GITHUB_HEADERS, timeout=10)
        if r.status_code == 200:
            data = r.json()
            raw = base64.b64decode(data.get("content", ""))
            return json.loads(raw), data.get("sha")
    except Exception:
        traceback.print_exc()
    return {"palavras": [], "frases": []}, None


def push_history(hist, sha=None):
    """
    Atualiza o histórico no GitHub se houver mudanças. Em caso de conflito, tenta refetch e reenviar.
    """
    # Verifica conteúdo atual para evitar PUT desnecessário
    try:
        current, current_sha = fetch_history()
        if current == hist:
            print(f"[HIST] Sem mudanças para enviar (sha={current_sha}).")
            return current_sha
    except Exception:
        traceback.print_exc()
    # Prepara payload
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_PATH}"
    content_b64 = base64.b64encode(json.dumps(hist, ensure_ascii=False, indent=2).encode()).decode()
    payload = {"message": "Atualiza histórico pelo bot", "content": content_b64, "branch": "main"}
    if sha:
        payload["sha"] = sha
    # Tenta enviar
    try:
        r = requests.put(url, headers=GITHUB_HEADERS, json=payload, timeout=10)
        if r.status_code == 409:
            # SHA mudou no repositório, refetch e reenviar
            _, new_sha = fetch_history()
            if new_sha and new_sha != sha:
                payload["sha"] = new_sha
                r = requests.put(url, headers=GITHUB_HEADERS, json=payload, timeout=10)
        print(f"[HIST PUT] status={r.status_code} text={r.text}")
        r.raise_for_status()
        # Atualiza sha retornado
        data = r.json()
        return data.get("content", {}).get("sha")
    except Exception:
        traceback.print_exc()
        return None

# --- Prompt & Parsing --- & Parsing ---
def build_prompt(palavras, frases):
    hist_text = ""
    if palavras:
        hist_text += "Palavras já usadas: " + ", ".join(palavras) + ".\n"
    if frases:
        hist_text += "Frases já usadas: " + ", ".join(frases) + ".\n"
    hist_text += (
        "Com base no histórico acima, gere APENAS uma nova palavra em inglês e uma nova frase estoica em português, "
        "sem repetir nenhuma usada; as palavras não precisam ser da área do estoicismo.\n"
        "Use formato (linha por campo, espaços entre pares, campo em negrito, texto normal na resposta):\n"
        "**Palavra**: <palavra>\n"
        "**Definição**: <definição em português>\n"
        "**Exemplo**: <exemplo em inglês>\n"
        "**Tradução**: <tradução em português>\n"
        "**Frase estoica**: <frase em português>\n"
        "**Explicação**: <explicação em português>"
    )
    return hist_text


def parse_block(raw):
    m = re.search(r'(?im)^Palavra:.*?Explicação:.*?(?=^Palavra:|\Z)', raw, re.DOTALL)
    return m.group(0).strip() if m else raw.strip()

# --- Generation & Update ---
async def generate_and_update():
    hist, sha = fetch_history()
    prompt = build_prompt(hist.get("palavras", []), hist.get("frases", []))
    resp = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[
            {"role": "system", "content": "Você é um professor de inglês e estoico."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    ).choices[0].message.content
    block = parse_block(resp)
    pw = re.search(r'(?im)^Palavra: *(.*)', block)
    fr = re.search(r'(?im)^Frase estoica: *(.*)', block)
    if pw:
        p = pw.group(1).strip()
        if p.lower() not in [x.lower() for x in hist["palavras"]]:
            hist["palavras"].append(p)
    if fr:
        f = fr.group(1).strip()
        if f.lower() not in [x.lower() for x in hist["frases"]]:
            hist["frases"].append(f)
    push_history(hist, sha)
    return block

# --- Chunking & Send ---
def chunk_text(txt, limit=1900):
    return [txt[i:i+limit] for i in range(0, len(txt), limit)]

async def send_content(chan):
    content = await generate_and_update()
    for segment in chunk_text(content):
        await safe_send(chan, segment)

# --- Commands ---
@bot.command()
async def ask(ctx, *, pergunta: str):
    if not autorizado(ctx) or not groq_client:
        return await ctx.send("❌ Não autorizado.")
    h = conversas[ctx.channel.id]
    h.append({"role": "user", "content": pergunta})
    msgs = [{"role": "system", "content": "Você é um assistente prestativo."}] + list(h)
    out = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=msgs,
        temperature=0.7
    ).choices[0].message.content
    h.append({"role": "assistant", "content": out})
    for seg in chunk_text(out):
        await safe_send(ctx.channel, seg)

@bot.command()
async def search(ctx, *, consulta: str):
    if not autorizado(ctx) or not SERPAPI_KEY:
        return await ctx.send("❌ Não autorizado.")
    await safe_send(ctx.channel, f"🔍 Buscando: {consulta}")
    items = GoogleSearch({"q": consulta, "hl": "pt-br", "gl": "br", "api_key": SERPAPI_KEY}).get_dict().get("organic_results", [])[:3]
    snippet = "\n\n".join(f"**{i['title']}**: {i['snippet']}" for i in items) or "Nenhum resultado."
    sumr = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[
            {"role": "system", "content": "Resuma resultados."},
            {"role": "user", "content": snippet}
        ],
        temperature=0.3
    ).choices[0].message.content
    for seg in chunk_text(sumr):
        await safe_send(ctx.channel, seg)

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

# --- Events ---
@bot.event
async def on_ready():
    import os, threading
    print(f"[READY] PID={os.getpid()} TID={threading.get_ident()} — Bot online: {bot.user}")
    daily_send.start()

# --- Main ---
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
