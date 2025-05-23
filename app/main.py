# -*- coding: utf-8 -*-
"""
main.py — BotDS Discord Bot
Conversa em “janelas” de áudio de 5 s via Gemini Live API.
Comandos: !call, !sair, !ask, !search
Keep-alive HTTP para Render
"""
import os, io, asyncio, subprocess, traceback
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict, deque

import discord
from discord.ext import commands, tasks
from discord import sinks, File
from dotenv import load_dotenv
from groq import Groq
from serpapi import GoogleSearch
import google.generativeai as genai  # pip install google-generativeai

# --- Environment ---
load_dotenv()
DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY")
GENAI_API_KEY    = os.getenv("GEMINI_API_KEY")
SERPAPI_KEY      = os.getenv("SERPAPI_KEY")
ALLOWED_GUILD_ID = int(os.getenv("ALLOWED_GUILD_ID","0"))
ALLOWED_USER_ID  = int(os.getenv("ALLOWED_USER_ID","0"))
PORT             = int(os.getenv("PORT","10000"))
LLAMA_MODEL      = os.getenv("LLAMA_MODEL","meta-llama/llama-4-scout-17b-16e-instruct")
GEMINI_MODEL     = "gemini-2.0-flash-live-001"

# Configura GenAI
genai.configure(api_key=GENAI_API_KEY)

# --- Keep-alive HTTP ---
class KeepAlive(BaseHTTPRequestHandler):
    def do_HEAD(self): self.send_response(200); self.end_headers()
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type","text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot online!")
Thread(target=lambda: HTTPServer(("0.0.0.0", PORT), KeepAlive).serve_forever(), daemon=True).start()

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states   = True
bot = commands.Bot(command_prefix="!", intents=intents)
_convo = defaultdict(lambda: deque(maxlen=10))
_voice = {}  # guild.id -> VoiceClient
_chunk_task = {}  # guild.id -> asyncio.Task
groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# --- Helpers ---
def autorizado(ctx):
    return ((isinstance(ctx.channel, discord.DMChannel) and ctx.author.id==ALLOWED_USER_ID)
            or (ctx.guild and ctx.guild.id==ALLOWED_GUILD_ID))
def chunk_text(txt, limit=2000): return [txt[i:i+limit] for i in range(0,len(txt),limit)]

# --- Loop de captura em janelas ---
async def _capture_loop(guild_id, channel):
    vc = _voice[guild_id]
    try:
        while True:
            sink = sinks.WaveSink()
            vc.start_recording(sink, lambda *a: None, None)
            await asyncio.sleep(5)  # janela de 5 s
            vc.stop_recording()
            # pegar apenas áudio do usuário que falou o comando (ou mixar todos)
            user_id = channel.guild.me.id
            audio = sink.audio_data.get(user_id)
            if not audio:
                continue
            data = audio.file.getvalue()  # WAV PCM16-48k
            # envia para Gemini Live
            session = await genai.live.connect(model=GEMINI_MODEL, modalities=["audio"])
            # convert WAV→PCM16k
            p = subprocess.Popen(
                ["ffmpeg","-i","pipe:0","-ar","16000","-ac","1","-f","s16le","pipe:1"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE
            )
            pcm16, _ = p.communicate(data)
            await session.send(audio=pcm16)
            # recebe e toca
            vc.stop()  # para qualquer áudio atual
            async for resp in session.receive():
                if hasattr(resp,"audio"):
                    with open("resp.pcm","wb") as f: f.write(resp.audio)
                    src = discord.FFmpegPCMAudio("resp.pcm", options="-f s16le -ar 48000 -ac 1")
                    vc.play(src)
            await session.close()
    except asyncio.CancelledError:
        pass
    except Exception:
        traceback.print_exc()

# --- Comandos de Voz ---
@bot.command()
async def call(ctx):
    """!call — entra no VC e inicia janelas de streaming."""
    if not autorizado(ctx): return await ctx.send("❌ Não autorizado.")
    if not GENAI_API_KEY: return await ctx.send("❌ GEMINI_API_KEY ausente.")
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.send("❌ Entre num canal de voz primeiro.")
    guild = ctx.guild.id
    if guild in _voice:
        return await ctx.send("⚠️ Já estou na call.")
    vc = await ctx.author.voice.channel.connect()
    _voice[guild] = vc
    await ctx.send(f"✅ Conectado em **{ctx.author.voice.channel.name}**, iniciando o chat de voz...")
    # dispara loop de captura
    _chunk_task[guild] = bot.loop.create_task(_capture_loop(guild, ctx))

@bot.command()
async def sair(ctx):
    """!sair — encerra streaming e sai do VC."""
    if not autorizado(ctx): return await ctx.send("❌ Não autorizado.")
    guild = ctx.guild.id
    task = _chunk_task.get(guild)
    if task:
        task.cancel()
    vc = _voice.get(guild)
    if vc:
        await vc.disconnect()
    _voice.pop(guild, None)
    _chunk_task.pop(guild, None)
    await ctx.send("👋 Saí da call e parei o chat de voz.")

# --- Comandos de Texto ---
@bot.command()
async def ask(ctx, *, pergunta: str):
    if not autorizado(ctx) or not groq: return await ctx.send("❌ Não autorizado ou Groq indisponível.")
    h = _convo[ctx.channel.id]; h.append({"role":"user","content":pergunta})
    msgs = [{"role":"system","content":"Você é um assistente prestativo."}]+list(h)
    out = groq.chat.completions.create(model=LLAMA_MODEL,messages=msgs,temperature=0.7).choices[0].message.content
    h.append({"role":"assistant","content":out})
    for p in chunk_text(out): await ctx.send(p)

@bot.command()
async def search(ctx, *, consulta: str):
    if not autorizado(ctx) or not SERPAPI_KEY: return await ctx.send("❌ Não autorizado ou SERPAPI_KEY ausente.")
    await ctx.send(f"🔍 Buscando: {consulta}")
    res = GoogleSearch({"q":consulta,"hl":"pt-br","gl":"br","api_key":SERPAPI_KEY}).get_dict().get("organic_results",[])[:3]
    snp = "\n\n".join(f"**{r['title']}**: {r['snippet']}" for r in res) or "Nenhum resultado."
    summ = groq.chat.completions.create(model=LLAMA_MODEL,messages=[{"role":"system","content":"Resuma resultados."},{"role":"user","content":snp}],temperature=0.3).choices[0].message.content
    for p in chunk_text(summ): await ctx.send(p)

@bot.event
async def on_ready():
    print(f"✅ Online: {bot.user} | Guilds: {len(bot.guilds)}")

# --- Run ---
if __name__=="__main__":
    bot.run(DISCORD_TOKEN)
