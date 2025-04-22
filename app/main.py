# -*- coding: utf-8 -*-
"""
main.py - BotDS Discord Bot com integração Groq e Google Generative AI
Correções:
- Ajuste de indentação nos blocos try/except
- Importação de base64
- Substituição de google_client_configured por google_client
- Implementação de send_long_message
- Definição de send_long_message e correção de referências
- Outros ajustes de segurança e robustez
"""
import base64
import discord
from discord.ext import commands, tasks
import os
import asyncio
import datetime
import json
import re
from collections import defaultdict, deque
from threading import Thread
from flask import Flask
from dotenv import load_dotenv

# Clients de API
from groq import Groq
from serpapi import GoogleSearch
import google.generativeai as genai
import aiohttp
import io
from PIL import Image
import traceback

# Tentativa de importação do uploader GitHub
try:
    from github_uploader import upload_to_github, HISTORICO_FILE_PATH
except ImportError:
    print("ERRO CRÍTICO: Não foi possível importar 'github_uploader'.")
    HISTORICO_FILE_PATH = "historico.json"
    async def upload_to_github():
        print("ERRO: Função upload_to_github não carregada.")
        return 500, {"message": "Upload function not loaded"}

# Carrega variáveis de ambiente
load_dotenv()

# Configurações
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
GOOGLE_AI_API_KEY = os.getenv("GOOGLE_AI_API_KEY")

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
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)
    print("✅ Cliente Groq configurado.")
else:
    groq_client = None
    print("⚠️ GROQ_API_KEY não encontrada. Comandos de chat desabilitados.")

if GOOGLE_AI_API_KEY:
    try:
        genai.configure(api_key=GOOGLE_AI_API_KEY)
        google_client = genai
        print("✅ Cliente Google Generative AI configurado.")
    except Exception as e:
        print(f"❌ Erro ao configurar Google Generative AI: {e}")
        google_client = None
else:
    google_client = None
    print("⚠️ GOOGLE_AI_API_KEY não encontrada. Comando !img desabilitado.")

# Bot Discord
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.dm_messages = True
bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)
conversas = defaultdict(lambda: deque(maxlen=10))

# Função utilitária para mensagens longas
async def send_long_message(ctx, message, limit: int = 2000):
    for i in range(0, len(message), limit):
        await ctx.send(message[i:i+limit])

# Verifica autorização
def autorizado(ctx):
    if isinstance(ctx.channel, discord.DMChannel):
        return ctx.author.id == ALLOWED_USER_ID
    if ctx.guild:
        return ctx.guild.id == ALLOWED_GUILD_ID
    return False

# Busca na web via SerpApi
def buscar_na_web(consulta: str) -> str:
    if not SERPAPI_KEY:
        return "Erro: SerpApi não configurada."
    try:
        search = GoogleSearch({"q": consulta, "hl": "pt-br", "gl": "br", "api_key": SERPAPI_KEY})
        resultados = search.get_dict()
        organic = resultados.get("organic_results", [])[:3]
        if not organic:
            return "Nenhum resultado relevante encontrado."
        respostas = []
        for res in organic:
            title = res.get("title", "")
            snippet = res.get("snippet", "")
            link = res.get("link", "")
            respostas.append(f"**{title}**: {snippet} ({link})")
        return "\n\n".join(respostas)
    except Exception as e:
        traceback.print_exc()
        return f"Erro interno ao buscar na web: {e}"

# Evento on_ready
@bot.event
async def on_ready():
    print(f"--- Bot Online: {bot.user} ---")
    if CANAL_DESTINO_ID:
        enviar_conteudo_diario.start()
    else:
        print("⚠️ CANAL_DESTINO_ID não definido. Task diário não iniciada.")

# Comando !ask
@bot.command()
async def ask(ctx, *, pergunta: str):
    if not groq_client:
        return await ctx.send("❌ Serviço de chat indisponível.")
    if not autorizado(ctx):
        return await ctx.send("❌ Usuário/servidor não autorizado.")
    hist = conversas[ctx.channel.id]
    hist.append({"role": "user", "content": pergunta})
    try:
        response = groq_client.chat.completions.create(
            model="llama-4-maverick-17b-128e-instruct",
            messages=list(hist),
            temperature=0.7
        )
        resposta = response.choices[0].message.content
        hist.append({"role": "assistant", "content": resposta})
        await send_long_message(ctx, resposta)
    except Exception as e:
        traceback.print_exc()
        # Remove mensagem de usuário ou assistente se necessário
        if hist and hist[-1]["role"] == "assistant":
            hist.pop()
        await ctx.send("❌ Erro ao processar sua pergunta.")

# Comando !search
@bot.command()
async def search(ctx, *, consulta: str):
    if not groq_client or not SERPAPI_KEY:
        return await ctx.send("❌ Serviço de busca+resumo indisponível.")
    if not autorizado(ctx):
        return await ctx.send("❌ Usuário/servidor não autorizado.")
    await ctx.send(f"🔍 Buscando na web por: {consulta}")
    dados = buscar_na_web(consulta)
    if dados.startswith("Erro"):
        return await ctx.send(dados)
    prompt = (
        f"Você recebeu a consulta: '{consulta}'.\n"
        f"Resultados da busca abaixo (baseie-se apenas neles):\n{dados}\n"
        "Responda de forma clara e objetiva em português brasileiro."
    )
    try:
        response = groq_client.chat.completions.create(
            model="llama-4-maverick-17b-128e-instruct",
            messages=[
                {"role": "system", "content": "Você resume resultados de busca com precisão."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        resp = response.choices[0].message.content
        await send_long_message(ctx, resp)
    except Exception as e:
        traceback.print_exc()
        await ctx.send("❌ Erro ao analisar resultados da busca.")

# Comando !testar_conteudo
@bot.command()
async def testar_conteudo(ctx):
    if not autorizado(ctx):
        return await ctx.send("❌ Usuário/servidor não autorizado.")
    conteudo = await gerar_conteudo_com_ia()
    await send_long_message(ctx, conteudo)

# Comando !img
@bot.command()
async def img(ctx, *, prompt: str):
    if not google_client:
        return await ctx.send("❌ API Google Generative AI não configurada.")
    if not autorizado(ctx):
        return await ctx.send("❌ Usuário/servidor não autorizado.")
    # Prepara contents
    contents = [{"parts": [{"text": prompt}]}]
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        img_bytes = await attachment.read()
        b64 = base64.b64encode(img_bytes).decode()
        contents.append({"parts": [{"inlineData": {"data": b64}}]})
    try:
        model = genai.GenerativeModel(model_name="gemini-2.0-flash-exp-image-generation")
        config = genai.GenerationConfig(response_modalities=["TEXT", "IMAGE"])
        response = await model.generate_content_async(contents=contents, generation_config=config)
    except TypeError as te:
        traceback.print_exc()
        # Fallback sem config explícita
        try:
            response = await model.generate_content_async(contents=contents)
        except Exception as e:
            traceback.print_exc()
            return await ctx.send("❌ Falha ao gerar imagem (fallback falhou).")
    except Exception as e:
        traceback.print_exc()
        return await ctx.send("❌ Erro interno ao chamar API Gemini.")
    # Processa resposta
    final_text = []
    image_data = None
    for part in response.candidates[0].content.parts:
        if getattr(part, 'text', None):
            final_text.append(part.text)
        elif getattr(part, 'inlineData', None) and part.inlineData.data:
            image_data = base64.b64decode(part.inlineData.data)
    if final_text:
        await send_long_message(ctx, "\n".join(final_text))
    if image_data:
        await ctx.send(file=discord.File(io.BytesIO(image_data), filename="gemini.png"))

# Conteúdo diário (9h00)
@tasks.loop(minutes=1)
async def enviar_conteudo_diario():
    now = datetime.datetime.now()
    if now.hour == 9 and now.minute == 0:
        if not CANAL_DESTINO_ID:
            return
        canal = bot.get_channel(CANAL_DESTINO_ID)
        if canal:
            conteudo = await gerar_conteudo_com_ia()
            await send_long_message(canal, conteudo)

@enviar_conteudo_diario.before_loop
async def before_enviar_conteudo_diario():
    await bot.wait_until_ready()

# Geração de conteúdo via Groq
async def gerar_conteudo_com_ia() -> str:
    if not groq_client:
        return "❌ Serviço de geração indisponível (sem chave Groq)."
    local = HISTORICO_FILE_PATH.split('/')[-1]
    try:
        with open(local, 'r', encoding='utf-8') as f:
            hist = json.load(f)
    except:
        hist = {'palavras': [], 'frases': []}
    recentes_pal = hist['palavras'][-5:]
    recentes_fra = hist['frases'][-5:]
    prompt = (
        f"Crie palavra em inglês (significado, exemplo, tradução) e frase estoica."
        f"Evite: {recentes_pal}, {recentes_fra}. Formato exato..."
    )
    try:
        resp = groq_client.chat.completions.create(
            model="llama-4-maverick-17b-128e-instruct",
            messages=[
                {"role": "system", "content": "Professor de inglês/estoico"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.85
        )
        return resp.choices[0].message.content
    except Exception as e:
        traceback.print_exc()
        return "⚠️ Falha ao gerar conteúdo."

# Keep-alive Flask
app = Flask(__name__)
@app.route('/')
def home():
    return f"Bot {bot.user.name if bot.user else ''} está online!"

def run_server():
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# Início
if __name__ == '__main__':
    if not DISCORD_TOKEN:
        print("ERRO: DISCORD_TOKEN não definido.")
    else:
        Thread(target=run_server, daemon=True).start()
        try:
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            traceback.print_exc()
