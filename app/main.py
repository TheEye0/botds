# -*- coding: utf-8 -*-
"""
main.py — BotDS Discord Bot
Agora com OpenAI GPT-4o (não Groq), comandos ask, search e keep-alive HTTP.
"""
import os
import re
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict, deque

import discord
from discord.ext import commands
from dotenv import load_dotenv
import openai
from serpapi import GoogleSearch

# --- Environment ---
load_dotenv()
DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY")
SERPAPI_KEY      = os.getenv("SERPAPI_KEY")
ALLOWED_GUILD_ID = int(os.getenv("ALLOWED_GUILD_ID", "0"))
ALLOWED_USER_ID  = int(os.getenv("ALLOWED_USER_ID", "0"))
PORT             = int(os.getenv("PORT", "10000"))
OPENAI_MODEL     = "gpt-4.1-mini-2025-04-14"

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

# Configura OpenAI
openai.api_key = OPENAI_API_KEY

# --- Helpers ---
def autorizado(ctx):
    return (
        (isinstance(ctx.channel, discord.DMChannel) and ctx.author.id == ALLOWED_USER_ID) or
        (ctx.guild and ctx.guild.id == ALLOWED_GUILD_ID)
    )

def chunk_text(text: str, limit: int = 2000):
    """Divide texto em pedaços <= limit caracteres."""
    return [text[i:i+limit] for i in range(0, len(text), limit)]

# --- Commands ---
@bot.command()
async def ask(ctx, *, pergunta: str):
    """Envia pergunta para IA OpenAI GPT-4o e retorna resposta com contexto."""
    if not autorizado(ctx) or not openai.api_key:
        return await ctx.send("❌ Não autorizado ou serviço indisponível.")
    hist_chan = conversas[ctx.channel.id]
    hist_chan.append({"role": "user", "content": pergunta})
    messages = [{"role": "system", "content": "Você é um assistente prestativo."}] + list(hist_chan)

    # Chama a API OpenAI
    response = openai.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        temperature=0.7
    )
    resp = response.choices[0].message.content
    hist_chan.append({"role": "assistant", "content": resp})

    # Envia em chunks para não exceder 2000 chars
    for piece in chunk_text(resp):
        await ctx.send(piece)

@bot.command()
async def search(ctx, *, consulta: str):
    """Busca na web com SerpApi e resume resultados."""
    if not autorizado(ctx) or not SERPAPI_KEY:
        return await ctx.send("❌ Não autorizado ou SERPAPI_KEY ausente.")
    await ctx.send(f"🔍 Buscando: {consulta}")

    results = GoogleSearch({
        "q": consulta,
        "hl": "pt-br",
        "gl": "br",
        "api_key": SERPAPI_KEY
    }).get_dict().get("organic_results", [])[:3]

    snippet = "\n\n".join(f"**{r['title']}**: {r['snippet']}" for r in results) or "Nenhum resultado."
    summary = openai.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": "Resuma resultados."},
            {"role": "user", "content": snippet}
        ],
        temperature=0.3
    ).choices[0].message.content

    for piece in chunk_text(summary):
        await ctx.send(piece)

# --- Events ---
@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")

# --- Main ---
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
