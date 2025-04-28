# -*- coding: utf-8 -*-
"""
main.py — BotDS Discord Bot - Versão simplificada com foco na resolução de duplicação
e problemas com histórico
"""
import os
import json
import traceback
import re
import base64
import requests
import discord
import asyncio
import time
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict, deque
from datetime import time as _time
from discord.ext import commands, tasks
from dotenv import load_dotenv
from groq import Groq
from serpapi import GoogleSearch

# --- Environment ---
load_dotenv()
DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY")
SERPAPI_KEY      = os.getenv("SERPAPI_KEY")
GITHUB_TOKEN     = os.getenv("GITHUB_TOKEN")
GITHUB_REPO      = os.getenv("GITHUB_REPO")
HISTORICO_PATH   = os.getenv("HISTORICO_FILE_PATH", "historico.json")
ALLOWED_GUILD_ID = int(os.getenv("ALLOWED_GUILD_ID", "0"))
ALLOWED_USER_ID  = int(os.getenv("ALLOWED_USER_ID", "0"))
DEST_CHANNEL_ID  = int(os.getenv("CANAL_DESTINO_ID", "0"))
LLAMA_MODEL      = os.getenv("LLAMA_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
PORT             = int(os.getenv("PORT", "10000"))

# --- HTTP Keep-alive ---
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot online!")
Thread(target=lambda: HTTPServer(("0.0.0.0", PORT), KeepAliveHandler).serve_forever(), daemon=True).start()

# --- Discord Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
conversas = defaultdict(lambda: deque(maxlen=10))
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# --- Anti-duplicação simples ---
# Usamos timestamps em vez de locks para evitar problemas
last_command_time = {}
COOLDOWN_SECONDS = 5  # Tempo mínimo entre comandos no mesmo canal

# --- Helpers ---
def autorizado(ctx):
    return ((isinstance(ctx.channel, discord.DMChannel) and ctx.author.id == ALLOWED_USER_ID)
            or (ctx.guild and ctx.guild.id == ALLOWED_GUILD_ID))

# --- GitHub Persistence ---
GITHUB_API_BASE = "https://api.github.com"
GITHUB_HEADERS = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

# Cache local do histórico para minimizar chamadas à API
_historico_cache = None
_historico_sha = None
_last_fetch_time = 0
CACHE_TIMEOUT = 60  # Segundos para expirar o cache

# --- GitHub Persistence revisitada ---
def fetch_history(force=False):
    """Busca o histórico do GitHub, ignorando cache se force=True."""
    global _historico_cache, _historico_sha, _last_fetch_time

    if not force and _historico_cache and (time.time() - _last_fetch_time) < CACHE_TIMEOUT:
        return _historico_cache, _historico_sha

    try:
        url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{HISTORICO_PATH}"
        resp = requests.get(url, headers=GITHUB_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        content_b64 = data.get("content", "")
        sha = data.get("sha")
        content = base64.b64decode(content_b64).decode("utf-8")
        hist_data = json.loads(content)
        # atualiza cache
        _historico_cache = hist_data
        _historico_sha   = sha
        _last_fetch_time = time.time()
        print(f"🔄 Histórico carregado (sha={sha})")
        return hist_data, sha
    except Exception as e:
        print("⚠️ Erro ao buscar histórico:", e)
        # se já tinha cache, devolve mesmo expirado
        if _historico_cache and _historico_sha:
            return _historico_cache, _historico_sha
        # caso contrário, retorna vazio
        return {"palavras": [], "frases": []}, None


def push_history(hist, sha):
    """Cria ou atualiza o historico.json no GitHub. Re-tenta em caso de conflito."""
    global _historico_cache, _historico_sha, _last_fetch_time

    content_json = json.dumps(hist, ensure_ascii=False, indent=2)
    content_b64  = base64.b64encode(content_json.encode("utf-8")).decode("ascii")

    payload = {
        "message": "Atualiza historico.json via bot",
        "content": content_b64,
    }
    # se tivermos SHA, é atualização; senão é criação
    if sha:
        payload["sha"] = sha

    url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{HISTORICO_PATH}"
    resp = requests.put(url, headers=GITHUB_HEADERS, json=payload, timeout=15)

    # conflito de SHA: máquina concorrente atualizou antes de nós
    if resp.status_code == 409:
        print("⚠️ Conflito de SHA — buscando novo SHA e re-tentando...")
        _historico_cache = None
        _, new_sha = fetch_history(force=True)
        return push_history(hist, new_sha)

    if resp.ok:
        data = resp.json().get("content", {})
        _historico_sha = data.get("sha")
        _last_fetch_time = time.time()
        # invalida cache para próxima leitura
        _historico_cache = None
        print(f"✅ Histórico salvo com sucesso (novo sha={_historico_sha})")
        return True
    else:
        print("❌ Erro ao salvar histórico:", resp.status_code, resp.text)
        return False

# --- Content Generation ---
async def gerar_conteudo_com_ia():
    import traceback  # só para garantir
    print("🔍 [DEBUG] Início de gerar_conteudo_com_ia()")
    if not groq_client:
        print("⚠️ [DEBUG] groq_client indisponível")
        return "⚠️ Serviço Groq indisponível."
    
    # 1) BUSCA HISTÓRICO
    print("🔍 [DEBUG] Antes de fetch_history()")
    hist, sha = fetch_history()
    print(f"🔄 [DEBUG] fetch_history retornou sha={sha!r} e hist={hist}")
    
    try:
        # 2) GERA CONTEÚDO — tenta até encontrar algo inédito
        prompt = (
            "Crie uma palavra em inglês (definição em português, exemplo em inglês e tradução).\n"
            "Depois, forneça uma frase estoica em português com explicação.\n"
            "Formato: uma linha por item: Palavra:..., Definição:..., Exemplo:..., Tradução:..., Frase estoica:..., Explicação:..."
        )

        MAX_TENTATIVAS = 5
        palavra = frase = None
        altered  = False

        for tentativa in range(1, MAX_TENTATIVAS + 1):
            # pede conteúdo à Groq
            resp = groq_client.chat.completions.create(
                model=LLAMA_MODEL,
                messages=[
                    {"role": "system", "content": "Você é um professor de inglês e estoico."},
                    {"role": "user",   "content": prompt}
                ],
                temperature=0.7
            )
            content_text = resp.choices[0].message.content.strip()
            print(f"🔁 [DEBUG] Tentativa {tentativa}: conteúdo bruto:\n{content_text}\n")

            # -------- LIMPA E EXTRAI ----------
            plain = content_text.replace("*", "")
            m1 = re.search(r'(?im)^palavra:\s*(.+)$', plain, flags=re.MULTILINE)
            m2 = re.search(r'(?im)^frase estoica:\s*(.+)$', plain, flags=re.MULTILINE)
            palavra = m1.group(1).strip() if m1 else None
            frase   = m2.group(1).strip() if m2 else None

            lower_palavras = [p.lower() for p in hist.get("palavras", [])]
            lower_frases   = [f.lower() for f in hist.get("frases", [])]

            # se ao menos um é novo, sai do loop
            if (palavra and palavra.lower() not in lower_palavras) or \
               (frase and frase.lower() not in lower_frases):
                altered = True
                print("✅ [DEBUG] Conteúdo inédito encontrado")
                break

            print("🔁 [DEBUG] Repetido, pedindo novamente…")

        # se não achou nada novo em 5 tentativas, altered permanece False
        
        # 3) Limpa todos os '*' e extrai com regex MULTILINE
        print(f"✏️ [DEBUG] Conteúdo bruto gerado:\n{content_text}\n")

        # remove todos os asteriscos
        plain = content_text.replace("*", "")
        print(f"🔍 [DEBUG] Conteúdo sem asteriscos (plain):\n{plain}\n")

        # extrai 'palavra' e 'frase estoica' no modo MULTILINE
        m1 = re.search(r'(?im)^palavra:\s*(.+)$', plain, flags=re.MULTILINE)
        m2 = re.search(r'(?im)^frase estoica:\s*(.+)$', plain, flags=re.MULTILINE)

        palavra = m1.group(1).strip() if m1 else None
        frase   = m2.group(1).strip() if m2 else None

        if palavra:
            print(f"🔎 [DEBUG] palavra extraída: {palavra!r}")
        else:
            print("🔍 [DEBUG] palavra NÃO encontrada")

        if frase:
            print(f"🔎 [DEBUG] frase extraída: {frase!r}")
        else:
            print("🔍 [DEBUG] frase NÃO encontrada")

        # 4) Verifica alterações
        altered = False
        if palavra and palavra.lower() not in [p.lower() for p in hist.get("palavras", [])]:
            hist.setdefault("palavras", []).append(palavra)
            print(f"➕ [DEBUG] Nova palavra adicionada: {palavra!r}")
            altered = True
        else:
            print("✔️ [DEBUG] Palavra repetida ou ausente")
        
        if frase and frase.lower() not in [f.lower() for f in hist.get("frases", [])]:
            hist.setdefault("frases", []).append(frase)
            print(f"➕ [DEBUG] Nova frase adicionada: {frase!r}")
            altered = True
        else:
            print("✔️ [DEBUG] Frase repetida ou ausente")
        
        # 5) Salva somente se alterou
        if altered:
            print("💾 [DEBUG] Alterações detectadas, chamando push_history()")
            saved = push_history(hist, sha)
            print(f"💾 [DEBUG] push_history retornou {saved}")
        else:
            print("💾 [DEBUG] Sem alterações, não chama push_history()")
    
    except Exception as e:
        print(f"❌ [DEBUG] Erro em gerar_conteudo_com_ia: {e}", traceback.format_exc())
        return f"⚠️ Erro ao gerar conteúdo: {e}"
    
    return content_text


# --- Verificação anti-duplicação ---
def check_cooldown(channel_id):
    """Verifica se pode executar outro comando no canal."""
    channel_id = str(channel_id)
    now = time.time()
    
    if channel_id in last_command_time:
        time_diff = now - last_command_time[channel_id]
        if time_diff < COOLDOWN_SECONDS:
            print(f"Comando rejeitado: cooldown ({time_diff:.2f}s < {COOLDOWN_SECONDS}s)")
            return False
    
    # Atualiza o timestamp
    last_command_time[channel_id] = now
    return True

# --- Comandos Discord ---
async def send_content(channel):
    """Envia conteúdo para o canal com proteção anti-duplicação."""
    channel_id = str(channel.id)
    
    # Verifica o cooldown
    if not check_cooldown(channel_id):
        await channel.send(f"⏳ Aguarde {COOLDOWN_SECONDS} segundos entre comandos.")
        return
    
    # Indica que está processando
    await channel.send("⌛ Gerando conteúdo...")
    
    # Gera e envia o conteúdo
    content = await gerar_conteudo_com_ia()
    await channel.send(content)

@bot.command()
async def ask(ctx, *, pergunta: str):
    """Comando para fazer perguntas ao bot."""
    if not autorizado(ctx) or not groq_client:
        return await ctx.send("❌ Não autorizado ou serviço indisponível.")
    
    channel_id = str(ctx.channel.id)
    if not check_cooldown(channel_id):
        return await ctx.send(f"⏳ Aguarde {COOLDOWN_SECONDS} segundos entre comandos.")
    
    # Indica que está processando
    await ctx.send("⌛ Pensando...")
    
    try:
        h = conversas[ctx.channel.id]
        h.append({"role": "user", "content": pergunta})
        
        resp = groq_client.chat.completions.create(
            model=LLAMA_MODEL,
            messages=[{"role": "system", "content": "Você é um assistente prestativo."}] + list(h),
            temperature=0.7
        ).choices[0].message.content
        
        h.append({"role": "assistant", "content": resp})
        await ctx.send(resp)
    except Exception as e:
        await ctx.send(f"❌ Erro: {str(e)}")

@bot.command()
async def search(ctx, *, consulta: str):
    """Comando para buscar informações na web."""
    if not autorizado(ctx) or not SERPAPI_KEY:
        return await ctx.send("❌ Não autorizado ou SERPAPI_KEY ausente.")
    
    channel_id = str(ctx.channel.id)
    if not check_cooldown(channel_id):
        return await ctx.send(f"⏳ Aguarde {COOLDOWN_SECONDS} segundos entre comandos.")
    
    await ctx.send(f"🔍 Buscando: {consulta}")
    
    try:
        results = GoogleSearch({"q": consulta, "hl": "pt-br", "gl": "br", "api_key": SERPAPI_KEY}).get_dict().get("organic_results", [])[:3]
        snippet = "\n".join(f"**{r['title']}**: {r['snippet']}" for r in results) or "Nenhum resultado."
        
        resumo = groq_client.chat.completions.create(
            model=LLAMA_MODEL,
            messages=[
                {"role": "system", "content": "Resuma resultados."},
                {"role": "user", "content": snippet}
            ],
            temperature=0.3
        ).choices[0].message.content
        
        await ctx.send(resumo)
    except Exception as e:
        await ctx.send(f"❌ Erro: {str(e)}")

@bot.command()
async def testar_conteudo(ctx):
    print("🛠️ [DEBUG] Entrou em testar_conteudo()", ctx.channel.id)
    """Comando para testar a geração de conteúdo."""
    if not autorizado(ctx):
        return await ctx.send("❌ Não autorizado.")
    
    await send_content(ctx.channel)

# --- Scheduled ---
@tasks.loop(time=_time(hour=9, minute=0))
async def daily_send():
    """Tarefa agendada para envio diário."""
    ch = bot.get_channel(DEST_CHANNEL_ID)
    if ch:
        await send_content(ch)

@bot.event
async def on_ready():
    """Evento disparado quando o bot está pronto."""
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")
    daily_send.start()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
