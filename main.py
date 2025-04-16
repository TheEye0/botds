ALLOWED_GUILD_ID = 692143509259419719
ALLOWED_USER_ID = 367664282990804992


from discord.ext import commands import discord import os
from groq import Groq
from dotenv import load_dotenv
from serpapi import GoogleSearch
from flask import Flask
from threading import Thread
from collections import defaultdict, deque

conversas = defaultdict(lambda: deque(maxlen=10))

load_dotenv()

# Configurações do Discord
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)

# Chaves das APIs (deixe no .env)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")  # Lembre de adicionar ao .env

# Inicializa o cliente Groq
groq_client = Groq(api_key=GROQ_API_KEY)

# Função de busca na web com SerpApi
def buscar_na_web(consulta):
    try:
        search = GoogleSearch({
            "q": consulta,
            "hl": "pt-br",
            "gl": "br",
            "api_key": SERPAPI_KEY
        })
        resultados = search.get_dict()

        respostas = []
        if "organic_results" in resultados:
            for resultado in resultados["organic_results"][:3]:
                titulo = resultado.get("title", "")
                snippet = resultado.get("snippet", "")
                respostas.append(f"{titulo}: {snippet}")

        return "\n".join(respostas) if respostas else "Nenhum resultado encontrado."
    except Exception as e:
        return f"Erro ao buscar na web: {e}"

@bot.event
async def on_ready():
    print(f"🤖 Bot conectado como {bot.user}")

# Verificação de autorização
def autorizado(ctx):
    # Permite se for no servidor autorizado
    if ctx.guild and ctx.guild.id == ALLOWED_GUILD_ID:
        return True
    # Permite se for uma DM com o usuário autorizado
    if ctx.guild is None and ctx.author.id == ALLOWED_USER_ID:
        return True
    return False

@bot.command()
async def ask(ctx, *, pergunta):
    if not autorizado(ctx):
        return await ctx.send("❌ Este bot só pode ser usado em um servidor autorizado.")

    canal_id = ctx.channel.id
    historico = conversas[canal_id]

    # Adiciona a nova pergunta ao histórico
    historico.append({"role": "user", "content": pergunta})

    mensagens = [{"role": "system", "content": "Você é um assistente útil e simpático."}] + list(historico)

    try:
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=mensagens
        )
        resposta = response.choices[0].message.content

        # Salva a resposta no histórico
        historico.append({"role": "assistant", "content": resposta})

        await ctx.send(resposta)

    except Exception as e:
        print(f"Erro: {e}")
        await ctx.send("❌ Ocorreu um erro ao processar sua pergunta.")

@bot.command()
async def search(ctx, *, consulta):
    if not autorizado(ctx):
        return await ctx.send("❌ Este bot só pode ser usado em um servidor autorizado.")

    await ctx.send("🔎 Buscando na internet...")

    dados = buscar_na_web(consulta)

    canal_id = ctx.channel.id
    historico = conversas[canal_id]

    prompt = f"""
    Um usuário fez a seguinte pergunta: \"{consulta}\".
    Aqui estão os resultados da pesquisa online:

    {dados}

    Responda de forma clara, útil e direta com base nesses dados:
    """

    historico.append({"role": "user", "content": prompt})

    mensagens = [{"role": "system", "content": "Você é um assistente inteligente e útil."}] + list(historico)

    try:
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=mensagens
        )
        resposta = response.choices[0].message.content

        historico.append({"role": "assistant", "content": resposta})

        await ctx.send(resposta)

    except Exception as e:
        print(f"Erro: {e}")
        await ctx.send("❌ Ocorreu um erro ao processar sua busca com IA.")

# ------ Servidor Flask ------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is online!"

def run_server():
    # O Render define a porta na variável de ambiente PORT
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# ------ Início da aplicação ------
if __name__ == "__main__":
    # Inicia o servidor Flask em uma thread
    Thread(target=run_server).start()


# Roda o bot
bot.run(DISCORD_TOKEN)
