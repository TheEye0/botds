# -*- coding: utf-8 -*-
"""
main.py — BotDS Discord Bot
OpenAI GPT-4o multimodal + SerpApi + Discord (imagem por attachment).
"""

import os
import io
import base64
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

OPENAI_MODEL     = "gpt-4o-2024-11-20"

openai.api_key = OPENAI_API_KEY

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
async def ask(ctx, *, pergunta: str = None):
    """Envia pergunta (texto e/ou imagem) para a OpenAI e retorna a resposta."""
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado ou serviço indisponível.")

    # Monta contexto da conversa
    hist_chan = conversas[ctx.channel.id]
    if pergunta:
        hist_chan.append({"role": "user", "content": pergunta})

    # Monta mensagem multimodal
    contents = []
    if pergunta:
        contents.append({"type": "text", "text": pergunta})
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        if attachment.content_type and attachment.content_type.startswith("image/"):
            img_bytes = await attachment.read()
            img_base64 = base64.b64encode(img_bytes).decode("utf-8")
            data_url = f"data:{attachment.content_type};base64,{img_base64}"
            contents.append({"type": "image_url", "image_url": {"url": data_url}})
        else:
            await ctx.send("⚠️ O arquivo anexado não é uma imagem suportada.")
            return

    # Se não for multimodal, envia como só texto
    if not contents:
        contents = [{"type": "text", "text": pergunta if pergunta else "Responda."}]

    # Chama a OpenAI API
    try:
        completion = openai.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": contents}],
            max_tokens=2048,
        )
        resp = completion.choices[0].message.content
        hist_chan.append({"role": "assistant", "content": resp})
        for piece in chunk_text(resp):
            await ctx.send(piece)
    except Exception as e:
        await ctx.send(f"❌ Erro: {e}")

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
            {"role": "system", "content": "Resuma resultados em português."},
            {"role": "user", "content": snippet}
        ],
        temperature=0.3
    ).choices[0].message.content

    for piece in chunk_text(summary):
        await ctx.send(piece)

@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")

# --- Main ---
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
