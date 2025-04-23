# -*- coding: utf-8 -*-
"""
main.py — BotDS Discord Bot
Integrado com Groq + SerpApi e persistência de histórico (palavras / frases estoicas) em um arquivo
`historico.json` hospedado no próprio repositório GitHub.

🔧 PRINCIPAIS CORREÇÕES
• Removido comando **!img** e restante de imports não usados.
• `send_long_message` convertido para utilitário síncrono simples (evita duplicação).  
• Eliminada a dupla declaração `@bot.command()` em **!testar_conteudo** e a linha solta que executava
`gerar_conteudo_com_ia()` na importação, causando segunda mensagem.  
• `salvar_historico()` chama `upload_to_github()` sem `await` (função síncrona).  
• `carregar_historico()` lê do GitHub **e** faz fallback para o cache local em
`LOCAL_HISTORY`.  
• Loop diário usa `ctx.send` direto (não “longo”) — só uma mensagem.
"""
import os, json, datetime, traceback, base64, requests
from collections import defaultdict, deque
from threading import Thread

import discord
from discord.ext import commands, tasks
from flask import Flask
from dotenv import load_dotenv
from groq import Groq
from serpapi import GoogleSearch

# ──────────────────── ENV ────────────────────
load_dotenv()
DISCORD_TOKEN  = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")
SERPAPI_KEY    = os.getenv("SERPAPI_KEY")
ALLOWED_GUILD  = int(os.getenv("ALLOWED_GUILD_ID", 0))
ALLOWED_USER   = int(os.getenv("ALLOWED_USER_ID", 0))
DEST_CHANNEL   = int(os.getenv("CANAL_DESTINO_ID", 0))
LLAMA_MODEL    = os.getenv("LLAMA_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN")
GITHUB_REPO    = os.getenv("GITHUB_REPO")
HIST_FILE_PATH = os.getenv("HISTORICO_FILE_PATH", "historico.json")

# ────────────────── Discord ──────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)
conversas = defaultdict(lambda: deque(maxlen=10))

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# ─────────────── Utilidades ────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_HISTORY = os.path.join(BASE_DIR, HIST_FILE_PATH)

def autorizado(ctx):
    return isinstance(ctx.channel, discord.DMChannel) and ctx.author.id == ALLOWED_USER or \
           ctx.guild and ctx.guild.id == ALLOWED_GUILD

def carregar_historico():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HIST_FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.ok:
            raw = base64.b64decode(r.json()["content"])
            with open(LOCAL_HISTORY, "wb") as f:
                f.write(raw)
            return json.loads(raw)
    except Exception:
        traceback.print_exc()
    try:
        with open(LOCAL_HISTORY, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"palavras": [], "frases": []}


def salvar_historico(hist: dict):
    try:
        with open(LOCAL_HISTORY, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
        from github_uploader import upload_to_github  # provided in repo
        upload_to_github()  # síncrono
    except Exception:
        traceback.print_exc()

# ────────── IA: conteúdo diário ───────────
async def gerar_conteudo_com_ia() -> str:
    if not groq_client:
        return "⚠️ Groq não configurado."
    hist = carregar_historico()
    prompt = (
        "Crie uma palavra em inglês (definição em pt, exemplo em en + tradução). "
        "Depois, uma frase estoica em pt com explicação. Formato exacto: \n"
        "Palavra: <palavra>\nDefinição: <def>\nExemplo: <ex>\nTradução do exemplo: <trad>\n"
        "Frase estoica: <frase>\nExplicação: <explicação>"
    )
    resposta = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[
            {"role": "system", "content": "Professor de inglês + filosofia estoica."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    ).choices[0].message.content.strip()

    # Atualiza histórico
    palavra = next((l.split(":",1)[1].strip() for l in resposta.splitlines() if l.lower().startswith("palavra:")), None)
    frase   = next((l.split(":",1)[1].strip() for l in resposta.splitlines() if l.lower().startswith("frase estoica:")), None)
    if palavra: hist["palavras"].append(palavra)
    if frase:   hist["frases"].append(frase)
    salvar_historico(hist)
    return resposta

# ───────── Loop diário ─────────
@tasks.loop(minutes=1)
async def enviar_conteudo_diario():
    now = datetime.datetime.now()
    if now.hour == 9 and now.minute == 0 and DEST_CHANNEL:
        canal = bot.get_channel(DEST_CHANNEL)
        if canal:
            await canal.send(await gerar_conteudo_com_ia())

# ─────────── Eventos ───────────
@bot.event
async def on_ready():
    print(f"✅ Bot online como {bot.user} — servidores: {len(bot.guilds)}")
    enviar_conteudo_diario.start()

# ─────────── Comandos ──────────
@bot.command()
async def ask(ctx, *, pergunta: str):
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    chat = conversas[ctx.channel.id]
    chat.append({"role": "user", "content": pergunta})
    resp = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=list(chat),
        temperature=0.7
    ).choices[0].message.content
    chat.append({"role": "assistant", "content": resp})
    await ctx.send(resp)

@bot.command()
async def search(ctx, *, consulta: str):
    if not autorizado(ctx) or not SERPAPI_KEY:
        return await ctx.send("❌ Não autorizado ou SERPAPI_KEY ausente.")
    await ctx.send(f"🔍 Pesquisando: {consulta}")
    results = GoogleSearch({"q": consulta, "hl": "pt-br", "gl": "br", "api_key": SERPAPI_KEY}).get_dict().get("organic_results", [])[:3]
    snippet = "\n\n".join(f"**{r['title']}**: {r['snippet']}" for r in results) or "Nenhum resultado."
    resumo = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[{"role":"system","content":"Resuma resultados."},{"role":"user","content":snippet}],
        temperature=0.3
    ).choices[0].message.content
    await ctx.send(resumo)

@bot.command()
async def testar_conteudo(ctx):
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    await ctx.send(await gerar_conteudo_com_ia())

# ────────── Keep‑alive Flask ─────────
app = Flask(__name__)
@app.route("/")
def home():
    return f"Bot {bot.user.name if bot.user else ''} online!"

def run_server():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)), use_reloader=False)

# ─────────── Main ────────────
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERRO: DISCORD_TOKEN não definido.")
    else:
        Thread(target=run_server, daemon=True).start()
        bot.run(DISCORD_TOKEN)
