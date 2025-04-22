# -*- coding: utf-8 -*-
"""
main.py - BotDS Discord Bot com integração Groq e automação de conteúdo diário

Correções e ajustes:
- Remoção total do gerador de imagens (!img)
- Reintrodução de histórico em historico.json com upload para GitHub
- Comando !testar_conteudo
- Fluxos de exceção com return antecipado para evitar duplicação de mensagens
- Inclusão completa de comando !search
- Definição de enviar_conteudo_diario antes de on_ready
- run_server para keep-alive com Flask
"""
import os
import json
import datetime
import traceback
import base64
import discord
from discord.ext import commands, tasks
from collections import defaultdict, deque
from threading import Thread
from flask import Flask
from dotenv import load_dotenv

# API Clients
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

# Inicialização do client Groq
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Discord bot setup
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

# Autorização
def autorizado(ctx):
    if isinstance(ctx.channel, discord.DMChannel):
        return ctx.author.id == ALLOWED_USER_ID
    if ctx.guild:
        return ctx.guild.id == ALLOWED_GUILD_ID
    return False

# Função para carregar histórico
def carregar_historico():
    try:
        with open(HISTORICO_FILE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {'palavras': [], 'frases': []}
    except Exception:
        return {'palavras': [], 'frases': []}

# Função para salvar histórico
def salvar_historico(hist):
    try:
        with open(HISTORICO_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
        # dispara upload para GitHub
        try:
            upload_to_github()
        except Exception:
            traceback.print_exc()
    except Exception:
        traceback.print_exc()

# Geração de conteúdo via Groq com histórico
async def gerar_conteudo_com_ia() -> str:
    if not groq_client:
        return "⚠️ Serviço de geração indisponível (sem chave Groq)."
    # Carrega histórico existente
    hist = carregar_historico()
    # Monta prompt solicitando JSON estruturado
    prompt = (
        "Por favor, responda apenas um objeto JSON com as chaves:
"
        "  - palavra: Palavra em inglês.
"
        "  - definicao: Definição da palavra em português.
"
        "  - exemplo: Exemplo de frase em inglês usando a palavra.
"
        "  - exemplo_traducao: Tradução da frase de exemplo para o português.
"
        "  - frase_estoica: Uma frase estoica.
"
        "  - explicacao_frase: Explicação da frase estoica em português.
"
        f"Evite repetir palavras ou frases já usadas: palavras anteriores {hist['palavras'][-5:]}, frases anteriores {hist['frases'][-5:]}."
    )
    try:
        resp = groq_client.chat.completions.create(
            model=LLAMA_MODEL,
            messages=[
                {"role": "system", "content": "Você formata saídas em JSON conforme solicitado."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )
        content = resp.choices[0].message.content
        # Tenta parsear JSON
        data = json.loads(content)
    except Exception:
        traceback.print_exc()
        return f"Resposta inesperada ou falha no JSON: {content}"
    # Atualiza histórico
    palavra = data.get('palavra')
    frase_est = data.get('frase_estoica')
    if palavra:
        hist['palavras'].append(palavra)
    if frase_est:
        hist['frases'].append(frase_est)
    salvar_historico(hist)
    # Monta resposta formatada
    return (
        f"**Palavra:** {data.get('palavra')}
"
        f"**Definição:** {data.get('definicao')}
"
        f"**Exemplo:** {data.get('exemplo')}
"
        f"**Tradução do exemplo:** {data.get('exemplo_traducao')}
"
        f"**Frase estoica:** {data.get('frase_estoica')}
"
        f"**Explicação:** {data.get('explicacao_frase')}"
    )

# Tarefa de conteúdo diário com histórico
async def gerar_conteudo_com_ia() -> str:
    if not groq_client:
        return "⚠️ Serviço de geração indisponível (sem chave Groq)."
    hist = carregar_historico()
    try:
        prompt = (
            f"Crie uma palavra em inglês (significado, exemplo) e uma frase estoica. "
            f"Evite repetir estas: {hist['palavras'][-5:]}, {hist['frases'][-5:]}."
        )
        resp = groq_client.chat.completions.create(
            model=LLAMA_MODEL,
            messages=[
                {"role": "system", "content": "Professor de inglês e estoico."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.85
        )
        conteúdo = resp.choices[0].message.content
        # extrai palavra e frase do conteúdo (assume linha 1 palavra, linha 2 frase)
        linhas = [l.strip() for l in conteúdo.split('\n') if l.strip()]
        if len(linhas) >= 2:
            palavra = linhas[0]
            frase = linhas[1]
            hist['palavras'].append(palavra)
            hist['frases'].append(frase)
            salvar_historico(hist)
        return conteúdo
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
    hist_chan = conversas[ctx.channel.id]
    hist_chan.append({"role": "user", "content": pergunta})
    try:
        resp = groq_client.chat.completions.create(
            model=LLAMA_MODEL,
            messages=list(hist_chan),
            temperature=0.7
        )
        texto = resp.choices[0].message.content
        hist_chan.append({"role": "assistant", "content": texto})
        await send_long_message(ctx, texto)
        return
    except Exception:
        traceback.print_exc()
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
