# -*- coding: utf-8 -*-
"""
main.py — BotDS Discord Bot
– Conecta em canal de voz e grava áudio em tempo real usando WaveSink
– Comandos: !call, !stop, !ask, !search
– Keep-alive HTTP para Render
"""
import os
import asyncio
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict, deque
import traceback

import discord
from discord.ext import commands
from discord import sinks, File
from dotenv import load_dotenv
from groq import Groq
from serpapi import GoogleSearch

# --- Environment ---
load_dotenv()
DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY")
SERPAPI_KEY      = os.getenv("SERPAPI_KEY")
ALLOWED_GUILD_ID = int(os.getenv("ALLOWED_GUILD_ID", "0"))
ALLOWED_USER_ID  = int(os.getenv("ALLOWED_USER_ID", "0"))
PORT             = int(os.getenv("PORT", "10000"))
LLAMA_MODEL      = os.getenv("LLAMA_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# --- Keep-alive HTTP Server ---
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
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
intents.voice_states   = True
bot = commands.Bot(command_prefix="!", intents=intents)
_conversations = defaultdict(lambda: deque(maxlen=10))
_voice_clients = {}  # guild.id -> VoiceClient

# --- Helpers ---
def autorizado(ctx):
    return (
        (isinstance(ctx.channel, discord.DMChannel) and ctx.author.id == ALLOWED_USER_ID)
        or (ctx.guild and ctx.guild.id == ALLOWED_GUILD_ID)
    )

def chunk_text(text: str, limit: int = 2000):
    return [text[i:i+limit] for i in range(0, len(text), limit)]

# --- Callback ao encerrar gravação ---
async def on_record_complete(sink: sinks.WaveSink, channel: discord.TextChannel):
    try:
        files = []
        for user_id, audio in sink.audio_data.items():
            # .file é um io.BytesIO contendo WAV PCM16LE 48k
            filename = f"{user_id}.wav"
            files.append(File(fp=audio.file, filename=filename))
        await sink.vc.disconnect()
        await channel.send(f"✅ Gravação finalizada para: {', '.join(f'<@{u}>' for u in sink.audio_data)}", files=files)
    except Exception:
        traceback.print_exc()
        await channel.send("⚠️ Erro ao processar a gravação.")

# --- Comandos de Voz ---
@bot.command()
async def call(ctx):
    """!call — entra no canal de voz e começa a gravar (WaveSink)."""
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    vc = _voice_clients.get(ctx.guild.id)
    if vc and vc.is_connected():
        return await ctx.send("⚠️ Já estou em um canal de voz.")
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.send("❌ Você precisa estar em um canal de voz.")
    channel = ctx.author.voice.channel
    vc = await channel.connect()
    _voice_clients[ctx.guild.id] = vc
    await ctx.send(f"🎙️ Conectado em **{channel.name}**, iniciando gravação...")
    sink = sinks.WaveSink()  # grava em PCM16LE WAV, 48 kHz
    vc.start_recording(sink, on_record_complete, ctx.channel)

@bot.command()
async def stop(ctx):
    """!stop — para a gravação e dispara o callback."""
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    vc = _voice_clients.get(ctx.guild.id)
    if not vc or not vc.is_connected():
        return await ctx.send("⚠️ Não estou gravando em nenhum canal.")
    vc.stop_recording()
    del _voice_clients[ctx.guild.id]
    await ctx.send("⏹️ Gravação parada, processando áudio...")

# --- Comandos de Chat ---
@bot.command()
async def ask(ctx, *, pergunta: str):
    """!ask — conversa mantendo contexto de até 10 mensagens."""
    if not autorizado(ctx) or not groq_client:
        return await ctx.send("❌ Não autorizado ou Groq indisponível.")
    hist = _conversations[ctx.channel.id]
    hist.append({"role":"user","content":pergunta})
    msgs = [{"role":"system","content":"Você é um assistente prestativo."}] + list(hist)
    resp = groq_client.chat.completions.create(
        model=LLAMA_MODEL, messages=msgs, temperature=0.7
    ).choices[0].message.content
    hist.append({"role":"assistant","content":resp})
    for part in chunk_text(resp):
        await ctx.send(part)

@bot.command()
async def search(ctx, *, consulta: str):
    """!search — busca web com SerpApi e resume resultados."""
    if not autorizado(ctx) or not SERPAPI_KEY:
        return await ctx.send("❌ Não autorizado ou SERPAPI_KEY ausente.")
    await ctx.send(f"🔍 Pesquisando: {consulta}")
    results = GoogleSearch({
        "q": consulta, "hl":"pt-br", "gl":"br", "api_key":SERPAPI_KEY
    }).get_dict().get("organic_results", [])[:3]
    snippet = "\n\n".join(f"**{r['title']}**: {r['snippet']}" for r in results) or "Nenhum resultado."
    summary = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[{"role":"system","content":"Resuma resultados."},{"role":"user","content":snippet}],
        temperature=0.3
    ).choices[0].message.content
    for part in chunk_text(summary):
        await ctx.send(part)

# --- Eventos ---
@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")

# --- Início ---
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
