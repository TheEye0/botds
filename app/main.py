# -*- coding: utf-8 -*-
"""
main.py — BotDS Discord Bot
Integra Groq, SerpApi e Google Gemini Live API para voz em tempo real,
comandos ask, search, call, sair e keep-alive HTTP.
"""
import os
import re
import subprocess
import asyncio
import traceback
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict, deque

import discord
from discord.ext import commands
from dotenv import load_dotenv
from groq import Groq
from serpapi import GoogleSearch
import opuslib  # pip install opuslib

# SDK Google Generative AI
import google.generativeai as genai

# --- Environment ---
load_dotenv()
DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY")
GENAI_API_KEY    = os.getenv("GEMINI_API_KEY")
SERPAPI_KEY      = os.getenv("SERPAPI_KEY")
ALLOWED_GUILD_ID = int(os.getenv("ALLOWED_GUILD_ID", "0"))
ALLOWED_USER_ID  = int(os.getenv("ALLOWED_USER_ID", "0"))
PORT             = int(os.getenv("PORT", "10000"))
LLAMA_MODEL      = os.getenv("LLAMA_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GEMINI_MODEL     = "gemini-2.0-flash-live-001"

# configura chave Gemini
genai.configure(api_key=GENAI_API_KEY)

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
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)
conversas = defaultdict(lambda: deque(maxlen=10))

groq_client   = Groq(api_key=GROQ_API_KEY)   if GROQ_API_KEY   else None

# --- Helpers ---
def autorizado(ctx):
    return (
      (isinstance(ctx.channel, discord.DMChannel) and ctx.author.id == ALLOWED_USER_ID)
      or (ctx.guild and ctx.guild.id == ALLOWED_GUILD_ID)
    )

def chunk_text(text: str, limit: int = 2000):
    return [text[i:i+limit] for i in range(0, len(text), limit)]

# --- PCM Recorder using opuslib ---
class PCMRecorder:
    """Grava áudio Opus do Discord e decodifica para PCM16le 48k."""
    def __init__(self):
        self.decoder = opuslib.Decoder(48000, 1)
        self.buffer = bytearray()
    def write(self, packet: discord.VoicePacket):
        # packet.data contém bytes Opus
        pcm = self.decoder.decode(packet.data, frame_size=960)  # ~20ms
        self.buffer.extend(pcm)
    def read(self) -> bytes:
        data = bytes(self.buffer)
        self.buffer.clear()
        return data

# --- Streaming Handlers ---
async def stream_audio_to_gemini(vc: discord.VoiceClient, session, recorder: PCMRecorder):
    """Converte PCM48k para PCM16k e envia à GenAI Live API."""
    ff = subprocess.Popen([
        "ffmpeg", "-f", "s16le", "-ar", "48000", "-ac", "1",
        "-i", "pipe:0", "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1"
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        while vc.is_connected():
            pcm48 = recorder.read()
            if not pcm48:
                await asyncio.sleep(0.1)
                continue
            ff.stdin.write(pcm48)
            ff.stdin.flush()
            pcm16 = ff.stdout.read(3200)  # ~0.2s at 16kHz
            if pcm16:
                await session.send(audio=pcm16)
    except Exception:
        traceback.print_exc()
    finally:
        ff.stdin.close(); ff.stdout.close(); ff.wait()
        await session.close()

async def stream_gemini_to_discord(vc: discord.VoiceClient, session):
    """Recebe áudio da GenAI e toca no canal via FFmpegPCMAudio."""
    async for chunk in session.receive():
        # chunk.audio contém PCM24k mono
        with open("resp.pcm", "wb") as f:
            f.write(chunk.audio)
        source = discord.FFmpegPCMAudio(
            "resp.pcm",
            options="-f s16le -ar 48000 -ac 1"
        )
        vc.play(source)
    await vc.disconnect()

# --- Commands ---
@bot.command()
async def call(ctx):
    """Entra no canal de voz e inicia stream para o Gemini."""
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    if not GENAI_API_KEY:
        return await ctx.send("❌ GEMINI_API_KEY ausente.")
    vc = ctx.voice_client
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.send("❌ Você precisa estar em um canal de voz.")
    vc = await ctx.author.voice.channel.connect()
    await ctx.send(f"✅ Conectado em **{ctx.author.voice.channel.name}**")
    # start listening
    recorder = PCMRecorder()
    vc.listen(recorder)
    # abre sessão Gemini Live
    session = await genai.live.connect(
        model=GEMINI_MODEL,
        modalities=["audio"]
    )
    bot.loop.create_task(stream_audio_to_gemini(vc, session, recorder))
    bot.loop.create_task(stream_gemini_to_discord(vc, session))

@bot.command()
async def sair(ctx):
    """Sai do canal de voz."""
    vc = ctx.voice_client
    if vc and vc.is_connected():
        vc.stop_listening()
        await vc.disconnect()
        await ctx.send("✅ Sai do canal de voz.")
    else:
        await ctx.send("❌ Não estou em um canal de voz.")

@bot.command()
async def ask(ctx, *, pergunta: str):
    """Envia pergunta para IA e retorna resposta com contexto."""
    if not autorizado(ctx) or not groq_client:
        return await ctx.send("❌ Não autorizado ou Groq indisponível.")
    h = conversas[ctx.channel.id]
    h.append({"role":"user","content":pergunta})
    msgs = [{"role":"system","content":"Você é um assistente prestativo."}] + list(h)
    out = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=msgs,
        temperature=0.7
    ).choices[0].message.content
    h.append({"role":"assistant","content":out})
    for piece in chunk_text(out):
        await ctx.send(piece)

@bot.command()
async def search(ctx, *, consulta: str):
    """Busca na web com SerpApi e resume resultados."""
    if not autorizado(ctx) or not SERPAPI_KEY:
        return await ctx.send("❌ Não autorizado ou SERPAPI_KEY ausente.")
    await ctx.send(f"🔍 Buscando: {consulta}")
    res = GoogleSearch({
        "q": consulta,
        "hl":"pt-br","gl":"br","api_key":SERPAPI_KEY
    }).get_dict().get("organic_results",[])[:3]
    snp = "\n\n".join(f"**{r['title']}**: {r['snippet']}" for r in res) or "Nenhum resultado."
    summ = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[
            {"role":"system","content":"Resuma resultados."},
            {"role":"user","content":snp}
        ],
        temperature=0.3
    ).choices[0].message.content
    for piece in chunk_text(summ):
        await ctx.send(piece)

# --- Events ---
@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")

# --- Main ---
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
