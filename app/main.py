ALLOWED_GUILD_ID = 692143509259419719
ALLOWED_USER_ID = 367664282990804992
CANAL_DESTINO_ID = 1359260714551873698  # Substitua pelo ID do canal desejado

import discord
from discord.ext import commands
import os
from groq import Groq
from dotenv import load_dotenv
from serpapi import GoogleSearch
from flask import Flask
from threading import Thread
from collections import defaultdict, deque
import datetime
import asyncio
from discord.ext import tasks
import json
import re
from github_uploader import upload_to_github

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


@bot.command()
async def testar_conteudo(ctx):
    if not autorizado(ctx):
        return await ctx.send("❌ Este bot só pode ser usado em um servidor autorizado.")

    conteudo = await gerar_conteudo_com_ia()
    await ctx.send(conteudo)


# Armazena histórico para evitar repetições
historico_palavras = set()
historico_frases = set()


@tasks.loop(minutes=1)
async def enviar_conteudo_diario():
    agora = datetime.datetime.now()
    if agora.hour == 9 and agora.minute == 0:
        canal = bot.get_channel(CANAL_DESTINO_ID)
        if canal:
            conteudo = await gerar_conteudo_com_ia()
            await canal.send(conteudo)
        await asyncio.sleep(60)  # Evita múltiplos envios às 09:00

@enviar_conteudo_diario.before_loop
async def before():
    await bot.wait_until_ready()


async def gerar_conteudo_com_ia():
    # ... (código anterior) ...
    try:
        with open("historico.json", "r", encoding="utf-8") as f:
            historico = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        historico = {"palavras": [], "frases": []}

    for _ in range(10):
        prompt = """
Crie duas coisas para um canal de aprendizado:

1. Uma palavra em inglês com:
- Significado
- Um exemplo de frase em inglês (com tradução).

2. Uma frase estoica com:
- Autor (se souber)
- Pequena explicação/reflexão em português.

Formato:
Palavra: ...
Significado: ...
Exemplo: ...
Tradução: ...

Frase estoica: "..."
Autor: ...
Reflexão: ...
"""
        try: # Início do try principal
            response = groq_client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {"role": "system", "content": "Você é um professor de inglês e filosofia estoica, escrevendo para um canal no Discord."},
                    {"role": "user", "content": prompt}
                ]
            )
            conteudo = response.choices[0].message.content

            match_palavra = re.search(r"(?i)^palavra:\s*(.+)", conteudo, re.MULTILINE)
            match_frase = re.search(r"(?i)^frase estoica:\s*\"?(.+?)\"?", conteudo, re.MULTILINE)

            if match_palavra and match_frase:
                palavra = match_palavra.group(1).strip()
                frase = match_frase.group(1).strip()

                if palavra not in historico["palavras"] and frase not in historico["frases"]:
                    historico["palavras"].append(palavra)
                    historico["frases"].append(frase)

                    # Salva localmente e depois tenta o upload DENTRO do 'with'
                    with open("historico.json", "w", encoding="utf-8") as f:
                        json.dump(historico, f, indent=2, ensure_ascii=False)
                        print("✅ Histórico salvo localmente em:", os.path.abspath("historico.json"))

                        # --- INÍCIO DO BLOCO MOVIDO E CORRIGIDO ---
                        try: # Adiciona um try/except específico para o upload (opcional, mas bom)
                            print("Tentando enviar historico.json para o GitHub...")
                            status, resp = upload_to_github()
                            if status == 201 or status == 200:
                                print("✅ Histórico atualizado no GitHub.")
                            else:
                                # Imprime mais detalhes do erro do GitHub
                                print(f"⚠️ Erro ao enviar para o GitHub (Status: {status}): {resp}")
                        except Exception as upload_err:
                            print(f"❌ Exceção durante o upload para o GitHub: {upload_err}")
                        # --- FIM DO BLOCO MOVIDO E CORRIGIDO ---

                    return conteudo # Retorna o conteúdo se tudo deu certo (save local + tentativa de upload)

            # Este else agora está no lugar certo em relação ao except principal
            else:
                print("⚠️ Não foi possível extrair a palavra ou frase do conteúdo gerado:")
                print(conteudo)
                # Corrigido: a lógica de impressão de repetido estava fora do lugar
                # Removido as linhas abaixo daqui pois 'palavra' e 'frase' podem não existir se o regex falhou
                # print("⚠️ Conteúdo repetido detectado.")
                # print("Palavra:", palavra)
                # print("Frase:", frase)

        # Este except agora segue corretamente o bloco try principal
        except Exception as e:
            # Mensagem mais específica para erro na geração/processamento
            print(f"❌ Erro ao gerar conteúdo ou processar resposta da IA: {e}")
            # Mantém o retorno original para sinalizar falha na geração
            return f"❌ Erro ao gerar conteúdo diário: {e}"

        # Adicionado um print se o conteúdo for repetido, dentro do loop
        if 'palavra' in locals() and 'frase' in locals() and (palavra in historico["palavras"] or frase in historico["frases"]):
             print(f"⚠️ Conteúdo repetido detectado (Palavra: '{palavra}', Frase: '{frase}'). Tentando novamente...")

        # Pequena pausa para não sobrecarregar a API em caso de repetições rápidas
        await asyncio.sleep(1)


    # Mensagem se o loop terminar sem sucesso
    print("⚠️ Não foi possível gerar um conteúdo inédito após 10 tentativas.")
    return "⚠️ Não foi possível gerar um conteúdo inédito após 10 tentativas."



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
    enviar_conteudo_diario.start()


# Roda o bot
bot.run(DISCORD_TOKEN)
