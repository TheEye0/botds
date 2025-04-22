# -*- coding: utf-8 -*-
"""
main.py - BotDS Discord Bot com integração Groq e Google Generative AI

Correções e versão final:
- Import correto de google.generativeai (genai)
- Configuração de ai_client com genai.Client
- Comando !testar_conteudo restaurado
- Comando !img usando genai.Image.create (sem types)
- Tratamento de exceções com returns antecipados
- Inclusão completa de comando !search
- Definição de enviar_conteudo_diario antes de on_ready
- run_server para keep-alive com Flask
"""
import base64
import discord
from discord.ext import commands, tasks
import os
import datetime
import traceback
from collections import defaultdict, deque
from threading import Thread
from flask import Flask
from dotenv import load_dotenv
import aiohttp
import io

# APIs
from groq import Groq, NotFoundError
from serpapi import GoogleSearch
import google.generativeai as genai

# Carrega variáveis de ambiente
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
GOOGLE_AI_API_KEY = os.getenv("GOOGLE_AI_API_KEY")
LLAMA_MODEL = os.getenv("LLAMA_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

# IDs permitidos
def _int_env(name):
    try:
        return int(os.getenv(name, "0"))
    except ValueError:
        return 0

ALLOWED_GUILD_ID = _int_env("ALLOWED_GUILD_ID")
ALLOWED_USER_ID = _int_env("ALLOWED_USER_ID")
CANAL_DESTINO_ID = _int_env("CANAL_DESTINO_ID")

# Inicialização dos clients
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
ai_client = None
if GOOGLE_AI_API_KEY:
    genai.configure(api_key=GOOGLE_AI_API_KEY)
    ai_client = genai

# Discord bot setup
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.dm_messages = True
bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)
conversas = defaultdict(lambda: deque(maxlen=10))

# Utilitário para mensagens longas
async def send_long_message(ctx, message: str, limit: int = 2000):
    for i in range(0, len(message), limit):
        await ctx.send(message[i:i+limit])

# Verifica autorização
def autorizado(ctx):
    if isinstance(ctx.channel, discord.DMChannel):
        return ctx.author.id == ALLOWED_USER_ID
    if ctx.guild:
        return ctx.guild.id == ALLOWED_GUILD_ID
    return False

# Geração de conteúdo via Groq
async def gerar_conteudo_com_ia() -> str:
    if not groq_client:
        return "⚠️ Serviço de geração indisponível (sem chave Groq)."
    try:
        prompt = "Crie palavra em inglês (significado, exemplo) e frase estoica."
        resp = groq_client.chat.completions.create(
            model=LLAMA_MODEL,
            messages=[
                {"role": "system", "content": "Professor de inglês/estoico"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.85
        )
        return resp.choices[0].message.content
    except Exception:
        traceback.print_exc()
        return "⚠️ Falha ao gerar conteúdo."

# Tarefa de conteúdo diário
@tasks.loop(minutes=1)
async def enviar_conteudo_diario():
    now = datetime.datetime.now()
    if now.hour == 9 and now.minute == 0 and CANAL_DESTINO_ID:
        canal = bot.get_channel(CANAL_DESTINO_ID)
        if canal:
            conteudo = await gerar_conteudo_com_ia()
            await send_long_message(canal, conteudo)

# Evento on_ready
@bot.event
async def on_ready():
    print(f"Bot online como {bot.user}")
    if CANAL_DESTINO_ID:
        enviar_conteudo_diario.start()

# Comando !ask
@bot.command()
async def ask(ctx, *, pergunta: str):
    if not groq_client:
        await ctx.send("❌ Serviço de chat indisponível.")
        return
    if not autorizado(ctx):
        await ctx.send("❌ Não autorizado.")
        return
    hist = conversas[ctx.channel.id]
    hist.append({"role": "user", "content": pergunta})
    try:
        resp = groq_client.chat.completions.create(
            model=LLAMA_MODEL,
            messages=list(hist),
            temperature=0.7
        )
        texto = resp.choices[0].message.content
        hist.append({"role": "assistant", "content": texto})
        await send_long_message(ctx, texto)
        return
    except NotFoundError:
        await ctx.send(f"❌ Modelo '{LLAMA_MODEL}' não encontrado. Ajuste LLAMA_MODEL.")
        return
    except Exception:
        traceback.print_exc()
        if hist and hist[-1]["role"] == "assistant":
            hist.pop()
        await ctx.send("❌ Erro ao processar a pergunta.")
        return

# Comando !search
@bot.command()
async def search(ctx, *, consulta: str):
    if not groq_client or not SERPAPI_KEY:
        await ctx.send("❌ Serviço de busca+resumo indisponível.")
        return
    if not autorizado(ctx):
        await ctx.send("❌ Não autorizado.")
        return
    await ctx.send(f"🔍 Buscando por: {consulta}")
    try:
        api = GoogleSearch({"q": consulta, "hl": "pt-br", "gl": "br", "api_key": SERPAPI_KEY})
        resultados = api.get_dict()
        organic = resultados.get("organic_results", [])[:3]
        if not organic:
            await ctx.send("Nenhum resultado relevante encontrado.")
            return
        respostas = [f"**{r.get('title')}**: {r.get('snippet')} ({r.get('link')})" for r in organic]
        snippet = "\n\n".join(respostas)
    except Exception:
        traceback.print_exc()
        await ctx.send("❌ Erro ao buscar na web.")
        return
    prompt = (
        f"Você recebeu a consulta: '{consulta}'.\n"
        f"Resultados da busca abaixo:\n{snippet}\n"
        "Responda em português claro e objetivo."
    )
    try:
        resp = groq_client.chat.completions.create(
            model=LLAMA_MODEL,
            messages=[
                {"role": "system", "content": "Você resume resultados com precisão."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        await send_long_message(ctx, resp.choices[0].message.content)
    except Exception:
        traceback.print_exc()
        await ctx.send("❌ Erro ao resumir resultados.")
        return

# Comando !testar_conteudo
@bot.command()
async def testar_conteudo(ctx):
    if not autorizado(ctx):
        await ctx.send("❌ Não autorizado.")
        return
    conteudo = await gerar_conteudo_com_ia()
    await send_long_message(ctx, conteudo)

# Comando !img usando Google Generative AI
@bot.command()
async def img(ctx, *, prompt: str):
    if not ai_client:
        await ctx.send("❌ Google AI não configurado.")
        return
    if not autorizado(ctx):
        await ctx.send("❌ Não autorizado.")
        return
    try:
        response = genai.Image.create(
            prompt=prompt,
            model="gemini-2.0-flash-exp-image-generation",
            size="1024x1024"
        )
        url = response["images"][0]["imageUri"]
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as r:
                data = await r.read()
                await ctx.send(file=discord.File(io.BytesIO(data), filename="image.png"))
    except Exception:
        traceback.print_exc()
        await ctx.send("❌ Erro ao gerar imagem com Google Generative AI.")
        return

# Keep-alive Flask
app = Flask(__name__)
@app.route('/')
def home():
    return f"Bot {bot.user.name if bot.user else ''} está online!"

# Keep-alive server
def run_server():
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# Execução do bot
if __name__ == '__main__':
    if not DISCORD_TOKEN:
        print("ERRO: DISCORD_TOKEN não definido.")
    else:
        Thread(target=run_server, daemon=True).start()
        bot.run(DISCORD_TOKEN)
