# -*- coding: utf-8 -*-
"""
main.py - BotDS Discord Bot com integração Groq e Google Gemini

Correções:
- Parametrização de modelo Llama via variável de ambiente LLAMA_MODEL
- Tratamento de exceções com return antecipado para evitar duplicações
- Comando !img refatorado para usar Gemini 2.0 Flash Experimental corretamente
- Inclusão de comando !search completo
- Definição de enviar_conteudo_diario antes de on_ready
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

# APIs
from groq import Groq, NotFoundError
from serpapi import GoogleSearch
import google.generativeai as genai
from google.genai import types

# Utilitários
import aiohttp
import io

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
if GOOGLE_AI_API_KEY:
    genai.configure(api_key=GOOGLE_AI_API_KEY)
    google_client = genai
else:
    google_client = None

# Configuração do Discord Bot
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.dm_messages = True
bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)
conversas = defaultdict(lambda: deque(maxlen=10))

# Função utilitária para mensagens longas
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
        return await ctx.send("❌ Serviço de chat indisponível.")
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
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
        return await send_long_message(ctx, texto)
    except NotFoundError:
        return await ctx.send(f"❌ Modelo '{LLAMA_MODEL}' não encontrado. Ajuste LLAMA_MODEL.")
    except Exception:
        traceback.print_exc()
        if hist and hist[-1]["role"] == "assistant":
            hist.pop()
        return await ctx.send("❌ Erro ao processar a pergunta.")

# Comando !search
@bot.command()
async def search(ctx, *, consulta: str):
    if not groq_client or not SERPAPI_KEY:
        return await ctx.send("❌ Serviço de busca+resumo indisponível.")
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    await ctx.send(f"🔍 Buscando por: {consulta}")
    try:
        search = GoogleSearch({"q": consulta, "hl": "pt-br", "gl": "br", "api_key": SERPAPI_KEY})
        resultados = search.get_dict()
        organic = resultados.get("organic_results", [])[:3]
        if not organic:
            return await ctx.send("Nenhum resultado relevante encontrado.")
        respostas = []
        for res in organic:
            respostas.append(f"**{res.get('title')}**: {res.get('snippet')} ({res.get('link')})")
        snippet = "\n\n".join(respostas)
    except Exception:
        traceback.print_exc()
        return await ctx.send("❌ Erro ao buscar na web.")
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
        return await send_long_message(ctx, resp.choices[0].message.content)
    except Exception:
        traceback.print_exc()
        return await ctx.send("❌ Erro ao resumir resultados.")

# Comando !img usando Gemini
@bot.command()
async def img(ctx, *, prompt: str):
    if not google_client:
        return await ctx.send("❌ Google AI não configurado.")
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    contents = [{"parts": [{"text": prompt}]}]
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        img_bytes = await attachment.read()
        b64 = base64.b64encode(img_bytes).decode()
        contents.append({"parts": [{"inlineData": {"data": b64}}]})
    try:
        model = google_client.GenerativeModel(model_name="gemini-2.0-flash-exp-image-generation")
        config = types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"])
        response = await model.generate_content_async(contents=contents, generation_config=config)
    except Exception:
        traceback.print_exc()
        return await ctx.send("❌ Erro ao gerar imagem com Gemini.")
    for part in response.candidates[0].content.parts:
        if getattr(part, 'text', None):
            await ctx.send(part.text)
        elif getattr(part, 'inlineData', None) and part.inlineData.data:
            data = base64.b64decode(part.inlineData.data)
            return await ctx.send(file=discord.File(io.BytesIO(data), filename="gemini.png"))
    return await ctx.send("❌ Nenhuma imagem gerada.")

# Keep-alive Flask
app = Flask(__name__)
@app.route('/')
def home():
    return f"Bot {bot.user.name if bot.user else ''} está online!"

# Execução do bot
if __name__ == '__main__':
    if not DISCORD_TOKEN:
        print("ERRO: DISCORD_TOKEN não definido.")
    else:
        Thread(target=run_server, daemon=True).start()
        bot.run(DISCORD_TOKEN)
