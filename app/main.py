# -*- coding: utf-8 -*-
"""
main.py — BotDS Discord Bot
Integra Groq + SerpApi e persiste histórico local em historico.json (com upload opcional ao GitHub).
"""
import os
import json
import datetime
import traceback
import base64
import requests
from collections import defaultdict, deque
from threading import Thread

import discord
from discord.ext import commands, tasks
from flask import Flask
from dotenv import load_dotenv
from groq import Groq
from serpapi import GoogleSearch

# --- Environment ---
load_dotenv()
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY    = os.getenv("GROQ_API_KEY")
SERPAPI_KEY     = os.getenv("SERPAPI_KEY")
ALLOWED_GUILD   = int(os.getenv("ALLOWED_GUILD_ID", "0"))
ALLOWED_USER    = int(os.getenv("ALLOWED_USER_ID", "0"))
DEST_CHANNEL    = int(os.getenv("CANAL_DESTINO_ID", "0"))
LLAMA_MODEL     = os.getenv("LLAMA_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

# GitHub upload optional
GITHUB_TOKEN    = os.getenv("GITHUB_TOKEN")
GITHUB_REPO     = os.getenv("GITHUB_REPO")

# Local history path
HISTORY_FILE    = os.path.join(
    os.path.dirname(__file__),
    os.getenv("HISTORICO_FILE_PATH", "historico.json")
)
# Path for GitHub API
HIST_FILE_PATH  = os.getenv("HISTORICO_FILE_PATH", "historico.json")

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
conversas = defaultdict(lambda: deque(maxlen=10))

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# --- Helpers ---
def autorizado(ctx):
    return (
        (isinstance(ctx.channel, discord.DMChannel) and ctx.author.id == ALLOWED_USER) or
        (ctx.guild and ctx.guild.id == ALLOWED_GUILD)
    )

# --- Histórico via GitHub API ---
def carregar_historico():
    """
    Faz GET no GitHub Contents API e retorna (histórico dict, sha string).
    Se o arquivo não existir, retorna estruturas vazias e sha None.
    """
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HIST_FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.ok:
            data = resp.json()
            raw = base64.b64decode(data.get("content", ""))
            hist = json.loads(raw)
            sha = data.get("sha")
            return hist, sha
    except Exception:
        traceback.print_exc()
    return {"palavras": [], "frases": []}, None


def salvar_historico(hist: dict, sha: str = None):
    """
    Faz PUT no GitHub Contents API para atualizar historico.json com novo conteúdo.
    Usa sha para sobrescrever a versão correta.
    """
    try:
        content_b64 = base64.b64encode(
            json.dumps(hist, ensure_ascii=False).encode()
        ).decode()
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HIST_FILE_PATH}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        payload = {
            "message": "Atualiza historico.json pelo bot",
            "content": content_b64,
            "branch": "main"
        }
        if sha:
            payload["sha"] = sha
        put_resp = requests.put(url, headers=headers, json=payload, timeout=10)
        put_resp.raise_for_status()
    except Exception:
        traceback.print_exc()

# --- Generate daily content ---
async def gerar_conteudo_com_ia() -> str:
    if not groq_client:
        return "⚠️ Serviço de geração indisponível."

    hist, sha = carregar_historico()
    prompt = (
        "Crie uma palavra em inglês (definição em português, exemplo em inglês e tradução)." +
        "Depois, forneça uma frase estoica em português com explicação.\n" +
        "Use este formato exato (uma linha por item):\n" +
        "Palavra: <palavra>\n" +
        "Definição: <definição em português>\n" +
        "Exemplo: <exemplo em inglês>\n" +
        "Tradução do exemplo: <tradução em português>\n" +
        "Frase estoica: <frase em português>\n" +
        "Explicação: <explicação em português>"
    )
    resp = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[
            {"role": "system", "content": "Você é um professor de inglês e filosofia estoica."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    ).choices[0].message.content.strip()

    lower = resp.lower()
    first = lower.find("palavra:")
    second = lower.find("palavra:", first + 1)
    if second != -1:
        resp = resp[:second].strip()

    palavra = None
    frase = None
    for line in resp.splitlines():
        if line.lower().startswith("palavra:"):
            palavra = line.split(":", 1)[1].strip()
        elif line.lower().startswith("frase estoica:"):
            frase = line.split(":", 1)[1].strip()

    updated = False
    if palavra and palavra.lower() not in [p.lower() for p in hist.get("palavras", [])]:
        hist.setdefault("palavras", []).append(palavra)
        updated = True
    if frase and frase.lower() not in [f.lower() for f in hist.get("frases", [])]:
        hist.setdefault("frases", []).append(frase)
        updated = True
    if updated:
        salvar_historico(hist, sha)

    return resp

# --- Daily loop ---
@tasks.loop(minutes=1)
async def enviar_conteudo_diario():
    now = datetime.datetime.now()
    if now.hour == 9 and now.minute == 0 and DEST_CHANNEL:
        chan = bot.get_channel(DEST_CHANNEL)
        if chan:
            await chan.send(await gerar_conteudo_com_ia())

# --- Events ---
@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")
    enviar_conteudo_diario.start()

# --- Commands ---
@bot.command()
async def ask(ctx, *, pergunta: str):
    if not autorizado(ctx) or not groq_client:
        return await ctx.send("❌ Não autorizado ou serviço indisponível.")
    # 1) Adiciona a pergunta ao histórico
    hist_chan = conversas[ctx.channel.id]
    hist_chan.append({"role": "user", "content": pergunta})

    # 2) Constrói o prompt com contexto
    mensagens = [{"role": "system", "content": "Você é um assistente prestativo."}] + list(hist_chan)

    # 3) Chama a API com histórico
    resp = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=mensagens,
        temperature=0.7
    ).choices[0].message.content

    # 4) Adiciona a resposta ao histórico
    hist_chan.append({"role": "assistant", "content": resp})

    # 5) Envia a resposta
    await ctx.send(resp)

@bot.command()
async def search(ctx, *, consulta: str):
    if not autorizado(ctx) or not SERPAPI_KEY:
        return await ctx.send("❌ Não autorizado ou SERPAPI_KEY ausente.")
    await ctx.send(f"🔍 Buscando: {consulta}")
    results = GoogleSearch({"q": consulta, "hl": "pt-br", "gl": "br", "api_key": SERPAPI_KEY}).get_dict().get("organic_results", [])[:3]
    snippet = "\n\n".join(f"**{r['title']}**: {r['snippet']}" for r in results) or "Nenhum resultado."
    resumo = groq_client.chat.completions.create(
        model=LLAMA_MODEL,
        messages=[{"role": "system", "content": "Resuma resultados."}, {"role": "user", "content": snippet}],
        temperature=0.3
    ).choices[0].message.content
    await ctx.send(resumo)

@bot.command()
async def testar_conteudo(ctx):
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    await ctx.send(await gerar_conteudo_com_ia())

# --- Keep-alive Flask ---
app = Flask(__name__)
@app.route("/")
def home():
    return f"Bot {bot.user.name if bot.user else ''} online!"

def run_server():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)), use_reloader=False)

# --- Main ---
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERRO: DISCORD_TOKEN não definido.")
    else:
        Thread(target=run_server, daemon=True).start()
        bot.run(DISCORD_TOKEN)
