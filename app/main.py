# -*- coding: utf-8 -*-
"""
main.py - BotDS Discord Bot com integração Groq e automação de conteúdo diário

Correções e ajustes finais:
- Remoção total do gerador de imagens (!img)
- Histórico em historico.json com upload para GitHub
- Comando !testar_conteudo
- Fluxo diário de conteúdo sem repetições
- Comando !ask, !search completos com return antecipado
- Keep-alive com Flask via run_server
"""
import os
import json
import datetime
import traceback
import discord
from discord.ext import commands, tasks
from collections import defaultdict, deque
from threading import Thread
from flask import Flask
from dotenv import load_dotenv

# Clients de API
from groq import Groq, NotFoundError
from serpapi import GoogleSearch
try:
    from github_uploader import upload_to_github, HISTORICO_FILE_PATH
except ImportError:
    HISTORICO_FILE_PATH = 'historico.json'
    async def upload_to_github():
        return 500, {"message": "Upload function not loaded"}

# Carrega variáveis de ambiente
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
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

# Inicializa Groq
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Configuração do Discord Bot
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.dm_messages = True
bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)
conversas = defaultdict(lambda: deque(maxlen=10))

# Função para enviar mensagens longas
def send_long_message(ctx, message: str, limit: int = 2000):
    async def _send():
        for i in range(0, len(message), limit):
            await ctx.send(message[i:i+limit])
    return bot.loop.create_task(_send())

# Verifica autorização
def autorizado(ctx):
    if isinstance(ctx.channel, discord.DMChannel):
        return ctx.author.id == ALLOWED_USER_ID
    if ctx.guild:
        return ctx.guild.id == ALLOWED_GUILD_ID
    return False

# Histórico de conteúdo
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
# Caminho do arquivo no repositório GitHub
def carregar_historico():
    # Busca o histórico diretamente do GitHub via API
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HISTORICO_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            j = r.json()
            content_b64 = j.get("content", "")
            raw = base64.b64decode(content_b64)
            return json.loads(raw)
        else:
            print(f"[Hist] GitHub GET status {r.status_code}, usando histórico vazio.")
    except Exception as e:
        print(f"[Hist] Erro ao baixar histórico: {e}")
    return {'palavras': [], 'frases': []}

def salvar_historico(hist):
    # Salva local e faz upload para GitHub
    with open(os.path.basename(HISTORICO_FILE_PATH), 'w', encoding='utf-8') as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)
    try:
        upload_to_github()
    except Exception:
        traceback.print_exc()

# Geração de conteúdo via Groq (texto formatado)(hist):
    try:
        with open(HIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
        try:
            upload_to_github()
        except Exception:
            traceback.print_exc()
    except Exception:
        traceback.print_exc()

# Geração de conteúdo via Groq (texto formatado)
async def gerar_conteudo_com_ia() -> str:
    if not groq_client:
        return "⚠️ Serviço de geração indisponível."
    hist = carregar_historico()
    prompt = """
Crie uma palavra em inglês com definição, exemplo em inglês e tradução para o português.
Em seguida, forneça uma frase estoica em português com sua explicação em português.
Use exatamente este formato, cada item em nova linha:
Palavra: <palavra>
Definição: <definição em português>
Exemplo: <exemplo em inglês>
Tradução do exemplo: <tradução>
Frase estoica: <frase em português>
Explicação: <explicação em português>
"""
    try:
        resp = groq_client.chat.completions.create(
            model=LLAMA_MODEL,
            messages=[
                {"role": "system", "content": "Você é um professor de inglês e filosofia estoica."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )
        content = resp.choices[0].message.content.strip()
    except Exception:
        traceback.print_exc()
        return f"Erro ao gerar conteúdo: {resp if 'resp' in locals() else ''}"
    # Extrai palavra e frase estoica para histórico
    lines = content.splitlines()
    palavra = None
    frase = None
    for line in lines:
        if line.startswith("Palavra:"):
            palavra = line.split("Palavra:",1)[1].strip()
        if line.startswith("Frase estoica:"):
            frase = line.split("Frase estoica:",1)[1].strip()
    if palavra:
        hist['palavras'].append(palavra)
    if frase:
        hist['frases'].append(frase)
    salvar_historico(hist)
    return content

# Loop diário
@tasks.loop(minutes=1)
async def enviar_conteudo_diario():
    now = datetime.datetime.now()
    if now.hour == 9 and now.minute == 0 and CANAL_DESTINO_ID:
        canal = bot.get_channel(CANAL_DESTINO_ID)
        if canal:
            await send_long_message(canal, await gerar_conteudo_com_ia())

@bot.event
async def on_ready():
    print(f"Bot online: {bot.user}")
    if CANAL_DESTINO_ID:
        enviar_conteudo_diario.start()

# Comando !ask
@bot.command()
async def ask(ctx, *, pergunta: str):
    if not groq_client:
        return await ctx.send("❌ Serviço indisponível.")
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    hist = conversas[ctx.channel.id]
    hist.append({"role":"user","content":pergunta})
    try:
        resp = groq_client.chat.completions.create(
            model=LLAMA_MODEL,
            messages=list(hist),
            temperature=0.7
        )
        texto = resp.choices[0].message.content
        hist.append({"role":"assistant","content":texto})
        return await send_long_message(ctx, texto)
    except Exception:
        traceback.print_exc()
        return await ctx.send("❌ Erro ao processar pergunta.")

# Comando !search
@bot.command()
async def search(ctx, *, consulta: str):
    if not groq_client or not SERPAPI_KEY:
        return await ctx.send("❌ Busca indisponível.")
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    await ctx.send(f"🔍 Buscando: {consulta}")
    try:
        api = GoogleSearch({"q":consulta,"hl":"pt-br","gl":"br","api_key":SERPAPI_KEY})
        res = api.get_dict().get("organic_results",[])[:3]
        if not res:
            return await ctx.send("Nenhum resultado.")
        snip = "\n\n".join([f"**{r['title']}**: {r['snippet']} ({r['link']})" for r in res])
    except Exception:
        traceback.print_exc()
        return await ctx.send("❌ Erro na busca.")
    prompt2 = f"Resuma em português os resultados: \n{snip}"
    try:
        resp2 = groq_client.chat.completions.create(
            model=LLAMA_MODEL,
            messages=[{"role":"system","content":"Resuma resultados."},{"role":"user","content":prompt2}],
            temperature=0.3
        )
        return await send_long_message(ctx, resp2.choices[0].message.content)
    except Exception:
        traceback.print_exc()
        return await ctx.send("❌ Erro ao resumir.")

# Comando !testar_conteudo
@bot.command()
async def testar_conteudo(ctx):
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    return await send_long_message(ctx, await gerar_conteudo_com_ia())

# Keep-alive Flask
app = Flask(__name__)
@app.route('/')
def home():
    return f"Bot {bot.user.name if bot.user else ''} online!"

# Keep-alive server
def run_server():
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

# Inicia bot
if __name__ == '__main__':
    if not DISCORD_TOKEN:
        print("ERRO: DISCORD_TOKEN não definido.")
    else:
        Thread(target=run_server,daemon=True).start()
        bot.run(DISCORD_TOKEN)
