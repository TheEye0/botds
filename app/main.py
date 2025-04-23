# -*- coding: utf-8 -*-
"""
main.py - BotDS Discord Bot com integração Groq, SerpApi e histórico no GitHub
"""
import os
import json
import datetime
import traceback
import base64
import requests
import discord
from discord.ext import commands, tasks
from collections import defaultdict, deque
from threading import Thread
from flask import Flask
from dotenv import load_dotenv

# --- Carrega variáveis de ambiente ---
load_dotenv()
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY")
SERPAPI_KEY     = os.getenv("SERPAPI_KEY")
ALLOWED_GUILD_ID  = int(os.getenv("ALLOWED_GUILD_ID", "0"))
ALLOWED_USER_ID   = int(os.getenv("ALLOWED_USER_ID", "0"))
CANAL_DESTINO_ID  = int(os.getenv("CANAL_DESTINO_ID", "0"))
LLAMA_MODEL       = os.getenv("LLAMA_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN")
GITHUB_REPO       = os.getenv("GITHUB_REPO")
HISTORICO_PATH    = os.getenv("HISTORICO_FILE_PATH", "historico.json")

# --- Inicializa clientes ---
from groq import Groq, NotFoundError
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
from serpapi import GoogleSearch

# --- Configuração Discord Bot ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.dm_messages = True
bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)
conversas = defaultdict(lambda: deque(maxlen=10))

# --- Helpers ---
async def send_long_message(ctx, text: str, limit: int = 2000):
    for i in range(0, len(text), limit):
        await ctx.send(text[i:i+limit])

def autorizado(ctx):
    if isinstance(ctx.channel, discord.DMChannel):
        return ctx.author.id == ALLOWED_USER_ID
    if ctx.guild:
        return ctx.guild.id == ALLOWED_GUILD_ID
    return False

# --- Histórico GitHub ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_HISTORY = os.path.join(BASE_DIR, HISTORICO_PATH)

def carregar_historico():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            data = r.json()
            raw = base64.b64decode(data.get('content', ''))
            with open(LOCAL_HISTORY, 'wb') as f:
                f.write(raw)
            return json.loads(raw)
    except Exception:
        traceback.print_exc()
    try:
        with open(LOCAL_HISTORY, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"palavras": [], "frases": []}

async def salvar_historico(hist):
    try:
        with open(LOCAL_HISTORY, 'w', encoding='utf-8') as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
        from github_uploader import upload_to_github
        # upload_to_github may be synchronous, call without await
        upload_to_github()
    except Exception:
        traceback.print_exc()
    except Exception:
        traceback.print_exc()

# --- Geração de conteúdo diário ---
async def gerar_conteudo_com_ia() -> str:
    if not groq_client:
        return "⚠️ Serviço de geração indisponível."
    hist = carregar_historico()
    prompt = """
Crie uma palavra em inglês com definição, exemplo em inglês e tradução para o português.
Em seguida, forneça uma frase estoica em português com explicação.
Formato (linhas):
Palavra: <palavra>
Definição: <definição em pt>
Exemplo: <exemplo en>
Tradução do exemplo: <tradução pt>
Frase estoica: <frase pt>
Explicação: <explicação pt>
"""
    resp = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[
            {"role":"system","content":"Você é um professor de inglês e filosofia estoica."},
            {"role":"user","content":prompt}
        ],
        temperature=0.7
    )
    content = resp.choices[0].message.content.strip()
    palavra = next((l.split(':',1)[1].strip() for l in content.splitlines() if l.startswith('Palavra:')), None)
    frase   = next((l.split(':',1)[1].strip() for l in content.splitlines() if l.startswith('Frase estoica:')), None)
    if palavra:
        hist['palavras'].append(palavra)
    if frase:
        hist['frases'].append(frase)
    await salvar_historico(hist)
    return content

@tasks.loop(minutes=1)
async def enviar_conteudo_diario():
    now = datetime.datetime.now()
    if now.hour == 9 and now.minute == 0 and CANAL_DESTINO_ID:
        chan = bot.get_channel(CANAL_DESTINO_ID)
        if chan:
            await send_long_message(chan, await gerar_conteudo_com_ia())

@bot.event
async def on_ready():
    print(f"Bot online: {bot.user}")
    if CANAL_DESTINO_ID:
        enviar_conteudo_diario.start()

# --- Comandos ---
@bot.command()
async def ask(ctx, *, pergunta: str):
    if not groq_client or not autorizado(ctx):
        return await ctx.send("❌ Serviço indisponível ou não autorizado.")
    hist = conversas[ctx.channel.id]
    hist.append({"role":"user","content":pergunta})
    resp = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=list(hist),
        temperature=0.7
    )
    texto = resp.choices[0].message.content
    hist.append({"role":"assistant","content":texto})
    await send_long_message(ctx, texto)

@bot.command()
async def search(ctx, *, consulta: str):
    if not SERPAPI_KEY or not autorizado(ctx):
        return await ctx.send("❌ Busca indisponível ou não autorizado.")
    await ctx.send(f"🔍 Buscando: {consulta}")
    resultados = GoogleSearch({"q":consulta,"hl":"pt-br","gl":"br","api_key":SERPAPI_KEY}).get_dict().get("organic_results",[])[:3]
    snippet = "\n\n".join(f"**{r['title']}**: {r['snippet']}" for r in resultados) or "Nenhum resultado."
    resp = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[
            {"role":"system","content":"Resuma isso de forma clara."},
            {"role":"user","content":snippet}
        ],
        temperature=0.3
    )
    await send_long_message(ctx, resp.choices[0].message.content)

@bot.command()
async def testar_conteudo(ctx):
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    await send_long_message(ctx, await gerar_conteudo_com_ia())

# --- Keep-alive Flask ---
app = Flask(__name__)
@app.route('/')
def home():
    return f"Bot {bot.user.name if bot.user else ''} online!"

def run_server():
    port = int(os.getenv('PORT',10000))
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    if not DISCORD_TOKEN:
        print("ERRO: DISCORD_TOKEN não definido.")
    else:
        Thread(target=run_server, daemon=True).start()
        bot.run(DISCORD_TOKEN)
